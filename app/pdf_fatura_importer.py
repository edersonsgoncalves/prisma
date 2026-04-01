"""
pdf_fatura_importer.py — FinOrg
================================
Extração, parsing e conciliação de faturas PDF de cartão.
Suporta múltiplos adicionais, transferências automáticas e adicionais adicionais.

Dependências: pip install pdfplumber python-dateutil
"""
from __future__ import annotations

import re, logging, uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    raise SystemExit("pip install pdfplumber")
try:
    from dateutil import parser as dateutil_parser
except ImportError:
    raise SystemExit("pip install python-dateutil")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pdf_fatura_importer")

SCORE_VALOR_EXATO   = 40
SCORE_DATA_EXATA    = 30
SCORE_DATA_3DIAS    = 15
SCORE_DATA_7DIAS    = 8
SCORE_MEMO_80       = 20
SCORE_MEMO_60       = 10
SCORE_PARCELA_IGUAL = 10
SCORE_DUPLICIDADE   = 200
THRESHOLD_FORTE     = 70
THRESHOLD_FRACO     = 40


# ═══════════════════════════════════════════════════════════════
# DATACLASSES
# ═══════════════════════════════════════════════════════════════

@dataclass
class adicionalInfo:
    adicional_nome:   str
    cartao_final:    str
    adicional_id:     Optional[int] = None
    apelido:         Optional[str] = None
    conta_vinculada: Optional[int] = None
    is_novo:         bool = False


@dataclass
class LancamentoFatura:
    data:          date
    descricao:     str
    valor:         float
    tipo:          str = "D"           # D=débito  C=crédito
    cartao_nome:   str = ""
    cartao_final:  str = ""
    adicional_id:   Optional[int] = None
    parcela_atual: Optional[int] = None
    parcela_total: Optional[int] = None
    hash_id:       str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    # Intenção definida pelo usuário na tela
    tipo_operacao: int = 3             # 1=Receita 3=Despesa 4=Transferência
    conta_destino: Optional[int] = None

    @property
    def parcela_str(self) -> Optional[str]:
        if self.parcela_atual and self.parcela_total:
            return f"{self.parcela_atual:03d}.{self.parcela_total:03d}"
        return None


@dataclass
class ResultadoConciliacao:
    lancamento:         LancamentoFatura
    score:              int
    classificacao:      str
    operacao_id:        Optional[int]   = None
    operacao_descricao: Optional[str]   = None
    operacao_data:      Optional[date]  = None
    operacao_valor:     Optional[float] = None


# ═══════════════════════════════════════════════════════════════
# EXTRATOR PDF — layout duas colunas
# ═══════════════════════════════════════════════════════════════

class PDFExtractor:
    DIVISAO_X = 0.505

    @classmethod
    def extrair_texto(cls, caminho: str | Path) -> str:
        esq, dir_ = [], []
        with pdfplumber.open(str(caminho)) as pdf:
            for p in pdf.pages:
                w, h = p.width, p.height
                m = w * cls.DIVISAO_X
                esq.append(p.crop((0, 0, m, h)).extract_text(x_tolerance=3, y_tolerance=3) or "")
                dir_.append(p.crop((m, 0, w, h)).extract_text(x_tolerance=3, y_tolerance=3) or "")
        return "\n".join(esq) + "\n" + "\n".join(dir_)

    @staticmethod
    def detectar_ano_fatura(texto: str) -> int:
        m = re.search(r"\b\d{2}/\d{2}/(\d{4})\b", texto)
        if m: return int(m.group(1))
        m2 = re.search(r"\b\d{2}/\d{2}/(\d{2})\b", texto)
        if m2: return 2000 + int(m2.group(1))
        return date.today().year

    @staticmethod
    def detectar_mes_fechamento(texto: str) -> Optional[int]:
        m = re.search(r"realizados\s+at[eé]\s+\d{2}/(\d{2})", texto, re.IGNORECASE)
        if m: return int(m.group(1))
        m2 = re.search(r"Vencimento\s+\d{2}/(\d{2})/\d{4}", texto, re.IGNORECASE)
        if m2: return int(m2.group(1))
        return None


# ═══════════════════════════════════════════════════════════════
# PARSERS
# ═══════════════════════════════════════════════════════════════

class BaseFaturaParser(ABC):

    @abstractmethod
    def pode_processar(self, texto: str) -> bool: ...

    @abstractmethod
    def extrair_lancamentos(self, texto: str, ano: int,
                            mes_fech: Optional[int]) -> list[LancamentoFatura]: ...

    @staticmethod
    def _parse_valor(raw: str) -> float:
        return float(raw.strip().replace(".", "").replace(",", "."))

    @staticmethod
    def _parse_data(dia_mes: str, ano: int) -> date:
        try:
            return datetime.strptime(f"{dia_mes}/{ano}", "%d/%m/%Y").date()
        except ValueError:
            return dateutil_parser.parse(f"{dia_mes}/{ano}", dayfirst=True).date()

    @staticmethod
    def _corrigir_ano(d: date, ano_fatura: int, mes_fech: int) -> date:
        if d.year == ano_fatura and d.month > mes_fech:
            return d.replace(year=ano_fatura - 1)
        return d

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().upper()


class SantanderParser(BaseFaturaParser):

    RE_SECAO = re.compile(
        r"^@?\s*([A-ZÁÉÍÓÚÀÂÃÊÎÔÛÇÄËÏÖÜ][A-ZÁÉÍÓÚÀÂÃÊÎÔÛÇÄËÏÖÜ\s\.]+?)"
        r"\s*[-–]\s*(?:\d{4}\s+XXXX\s+XXXX\s+|XXXX\s+XXXX\s+)(\d{4})\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    RE_LANC = re.compile(
        r"^(?:(\d{1,2})\s+)?(\d{2}/\d{2})\s+(.+?)\s+(?:(\d{2}/\d{2})\s+)?(-?[\d\.]+,\d{2})\s*$",
        re.MULTILINE,
    )
    RE_IGN = re.compile(
        r"^(Compra\s+Data|VALOR TOTAL|Pagamento e Demais|Parcelamentos?|Despesas?|"
        r"Detalhamento|Resumo|Saldo|Total\s+Despesas|[=\-]{4,}|\d+/\d+\s*$|"
        r"Descri|Cr.ditos|SuperCr|Saque|ANUIDADE|Explore|Acesse|"
        r"WWW\.|PREOCUPE|CONDICAO|DESCONTO|^\d+/\d+$)",
        re.IGNORECASE,
    )
    RE_IGN_DESC = re.compile(r"PAGAMENTO DE FATURA|ANUIDADE DIFERENCIADA", re.IGNORECASE)

    def pode_processar(self, texto: str) -> bool:
        return bool(re.search(r"SANTANDER", texto, re.IGNORECASE))

    def extrair_lancamentos(self, texto: str, ano: int,
                            mes_fech: Optional[int] = None) -> list[LancamentoFatura]:
        mf = mes_fech or date.today().month
        secoes = self._secoes(texto)
        log.info(f"  {len(secoes)} seção(ões) de adicional encontradas:")
        result = []
        for nome, final, bloco in secoes:
            log.info(f"    → {nome} ...{final}")
            novos = self._bloco(bloco, nome, final, ano, mf)
            log.info(f"    {nome} ...{final}: {len(novos)} lançamentos")
            result.extend(novos)
        log.info(f"  Total: {len(result)} lançamentos extraídos")
        return result

    def _secoes(self, texto: str) -> list[tuple[str, str, str]]:
        ms = list(self.RE_SECAO.finditer(texto))
        if not ms:
            return [("TITULAR", "0000", texto)]
        out = []
        for i, m in enumerate(ms):
            nome  = self._norm(m.group(1))
            final = m.group(2).strip()
            ini   = m.end()
            fim   = ms[i + 1].start() if i + 1 < len(ms) else len(texto)
            out.append((nome, final, texto[ini:fim]))
        return out

    def _bloco(self, bloco: str, adicional: str, final: str,
               ano: int, mf: int) -> list[LancamentoFatura]:
        result = []
        for linha in bloco.splitlines():
            linha = linha.strip()
            if not linha or self.RE_IGN.match(linha):
                continue
            m = self.RE_LANC.match(linha)
            if not m:
                continue
            dr, desc, pi, vr = m.group(2), m.group(3), m.group(4), m.group(5)
            if self.RE_IGN_DESC.search(desc):
                continue
            try:
                vf = self._parse_valor(vr)
            except ValueError:
                continue
            tipo  = "C" if vf < 0 else "D"
            valor = abs(vf)
            try:
                d = self._parse_data(dr, ano)
                d = self._corrigir_ano(d, ano, mf)
            except Exception:
                continue
            pa = pt = None
            if pi:
                try:
                    pts = pi.split("/"); pa, pt = int(pts[0]), int(pts[1])
                except Exception:
                    pass
            result.append(LancamentoFatura(
                data=d, descricao=self._norm(desc), valor=valor, tipo=tipo,
                cartao_nome=adicional, cartao_final=final,
                parcela_atual=pa, parcela_total=pt,
            ))
        return result


PARSERS_DISPONIVEIS: list[BaseFaturaParser] = [SantanderParser()]


def selecionar_parser(texto: str) -> BaseFaturaParser:
    for p in PARSERS_DISPONIVEIS:
        if p.pode_processar(texto):
            return p
    raise ValueError("Nenhum parser reconhece este layout de fatura.")


# ═══════════════════════════════════════════════════════════════
# REPOSITÓRIO DE adicionais
# ═══════════════════════════════════════════════════════════════

class adicionalRepository:
    def __init__(self, db):
        self._db = db

    def buscar(self, conta_id: int, cartao_final: str) -> Optional[dict]:
        from app.models import CartaoAdicional
        r = self._db.query(CartaoAdicional).filter(
            CartaoAdicional.conta_id == conta_id,
            CartaoAdicional.cartao_final == cartao_final,
            CartaoAdicional.ativo == 1
        ).first()
        if r:
            return {
                "adicional_id": r.adicional_id,
                "adicional_nome": r.adicional_nome,
                "cartao_final": r.cartao_final,
                "apelido": r.apelido,
                "conta_vinculada": r.conta_vinculada,
                "titular": r.titular
            }
        return None

    def listar(self, conta_id: int) -> list[dict]:
        from app.models import CartaoAdicional
        rows = self._db.query(CartaoAdicional).filter(
            CartaoAdicional.conta_id == conta_id
        ).order_by(CartaoAdicional.titular.desc(), CartaoAdicional.adicional_nome).all()
        return [{
            "adicional_id": r.adicional_id,
            "adicional_nome": r.adicional_nome,
            "cartao_final": r.cartao_final,
            "apelido": r.apelido,
            "conta_vinculada": r.conta_vinculada,
            "titular": r.titular
        } for r in rows]

    def criar(self, conta_id: int, adicional_nome: str, cartao_final: str,
              apelido: Optional[str] = None, conta_vinculada: Optional[int] = None,
              titular: bool = False) -> int:
        from app.models import CartaoAdicional
        # Tenta buscar existente para atualizar ou reativar
        ex = self._db.query(CartaoAdicional).filter(
            CartaoAdicional.conta_id == conta_id,
            CartaoAdicional.cartao_final == cartao_final
        ).first()
        
        if ex:
            ex.adicional_nome = adicional_nome
            ex.apelido = apelido or ex.apelido
            ex.conta_vinculada = conta_vinculada or ex.conta_vinculada
            ex.ativo = 1
            self._db.commit()
            return ex.adicional_id
        
        novo = CartaoAdicional(
            conta_id=conta_id,
            adicional_nome=adicional_nome,
            cartao_final=cartao_final,
            apelido=apelido,
            conta_vinculada=conta_vinculada,
            titular=1 if titular else 0
        )
        self._db.add(novo)
        self._db.commit()
        return novo.adicional_id

    def atualizar(self, adicional_id: int, apelido: Optional[str],
                  conta_vinculada: Optional[int]):
        from sqlalchemy import text
        self._db.execute(text("""
            UPDATE cartoes_adicionais
               SET apelido = COALESCE(:a, apelido), conta_vinculada = :v
             WHERE adicional_id = :pid
        """), {"a": apelido, "v": conta_vinculada, "pid": adicional_id})
        self._db.commit()

    def detectar(self, lancamentos: list[LancamentoFatura],
                 conta_id: int) -> list[adicionalInfo]:
        vistos: dict[str, adicionalInfo] = {}
        for l in lancamentos:
            if l.cartao_final in vistos:
                continue
            ex = self.buscar(conta_id, l.cartao_final)
            if ex:
                vistos[l.cartao_final] = adicionalInfo(
                    adicional_nome=ex["adicional_nome"], cartao_final=l.cartao_final,
                    adicional_id=ex["adicional_id"], apelido=ex["apelido"],
                    conta_vinculada=ex["conta_vinculada"], is_novo=False,
                )
            else:
                vistos[l.cartao_final] = adicionalInfo(
                    adicional_nome=l.cartao_nome, cartao_final=l.cartao_final, is_novo=True,
                )
        return list(vistos.values())


# ═══════════════════════════════════════════════════════════════
# MATCHER
# ═══════════════════════════════════════════════════════════════

class FaturaMatcher:

    def calcular_score(self, lanc: LancamentoFatura, op: dict) -> int:
        if op.get("pdf_hash") and op["pdf_hash"] == lanc.hash_id:
            return SCORE_DUPLICIDADE
        score = 0
        if abs(abs(float(op.get("operacoes_valor", 0))) - lanc.valor) < 0.02:
            score += SCORE_VALOR_EXATO
        d = op.get("operacoes_data_lancamento")
        if d:
            diff = abs((d - lanc.data).days)
            if diff == 0:   score += SCORE_DATA_EXATA
            elif diff <= 3: score += SCORE_DATA_3DIAS
            elif diff <= 7: score += SCORE_DATA_7DIAS
        dl = lanc.descricao.lower()
        do = (op.get("operacoes_descricao") or "").lower()
        if dl and do:
            r = SequenceMatcher(None, dl, do).ratio()
            if r >= 0.80:   score += SCORE_MEMO_80
            elif r >= 0.60: score += SCORE_MEMO_60
        if lanc.parcela_str and op.get("operacoes_parcela") == lanc.parcela_str:
            score += SCORE_PARCELA_IGUAL
        return score

    def classificar(self, score: int) -> str:
        if score >= SCORE_DUPLICIDADE: return "DUPLICIDADE"
        if score >= THRESHOLD_FORTE:   return "MATCH_FORTE"
        if score >= THRESHOLD_FRACO:   return "MATCH_FRACO"
        return "NOVO"

    def melhor_match(self, lanc: LancamentoFatura,
                     candidatos: list[dict]) -> tuple[Optional[dict], int]:
        melhor, ms = None, 0
        for c in candidatos:
            s = self.calcular_score(lanc, c)
            if s > ms: ms, melhor = s, c
        return melhor, ms


# ═══════════════════════════════════════════════════════════════
# REPOSITÓRIO DE OPERAÇÕES
# ═══════════════════════════════════════════════════════════════

class FaturaRepository:

    def __init__(self, db):
        self._db = db

    def buscar_candidatos(self, lanc: LancamentoFatura, janela: int = 7) -> list[dict]:
        from sqlalchemy import text
        rows = self._db.execute(text("""
            SELECT operacoes_id, operacoes_data_lancamento, operacoes_descricao,
                   operacoes_valor, operacoes_parcela, operacoes_efetivado,
                   operacoes_conta, operacoes_fatura
            FROM operacoes
            WHERE ABS(ABS(operacoes_valor) - :v) < 0.05
              AND operacoes_data_lancamento BETWEEN :dmin AND :dmax
              AND operacoes_validacao = 1
        """), {"v": lanc.valor,
               "dmin": lanc.data - timedelta(days=janela),
               "dmax": lanc.data + timedelta(days=janela)}
        ).mappings().all()
        return [dict(r) for r in rows]

    def efetivar_operacao(self, op_id: int, data_efetivado: date,
                          adicional_id: Optional[int] = None):
        from sqlalchemy import text
        self._db.execute(text("""
            UPDATE operacoes
               SET operacoes_efetivado = 1,
                   operacoes_data_efetivado = :dt,
                   operacoes_adicional_id = COALESCE(:pid, operacoes_adicional_id)
             WHERE operacoes_id = :id
        """), {"dt": data_efetivado, "pid": adicional_id, "id": op_id})
        self._db.commit()
        log.info(f"  ✅ #{op_id} efetivado")

    def inserir_operacao(self, lanc: LancamentoFatura, conta_id: int,
                         fatura_id: Optional[int] = None) -> int:
        from app.models import Operacao
        tipo_op  = lanc.tipo_operacao or (1 if lanc.tipo == "C" else 3)
        valor_db = -abs(lanc.valor) if lanc.tipo == "D" else abs(lanc.valor)
        
        nova = Operacao(
            operacoes_data_lancamento=lanc.data,
            operacoes_descricao=lanc.descricao,
            operacoes_conta=conta_id,
            operacoes_valor=valor_db,
            operacoes_tipo=tipo_op,
            operacoes_parcela=lanc.parcela_str,
            operacoes_efetivado=1,
            operacoes_data_efetivado=datetime.combine(lanc.data, datetime.min.time()),
            operacoes_validacao=1,
            operacoes_fatura=fatura_id,
            operacoes_adicional_id=lanc.adicional_id
        )
        self._db.add(nova)
        self._db.commit()
        log.info(f"  ➕ #{nova.operacoes_id} | {lanc.descricao} | R${lanc.valor:.2f} tipo={tipo_op}")
        return nova.operacoes_id

    def inserir_transferencia(self, lanc: LancamentoFatura, conta_origem: int,
                              conta_destino: int,
                              fatura_id: Optional[int] = None) -> tuple[int, int]:
        """Gera par saída (cartão) + entrada (conta destino) vinculados por transf_rel."""
        from sqlalchemy import text
        from app.models import Operacao
        import uuid as _uuid
        grupo = f"TRF-{_uuid.uuid4().hex[:10]}"

        # Lado da Saída (Cartão)
        saida = Operacao(
            operacoes_data_lancamento=lanc.data,
            operacoes_descricao=lanc.descricao,
            operacoes_conta=conta_origem,
            operacoes_valor=-abs(lanc.valor),
            operacoes_tipo=4,
            operacoes_parcela=lanc.parcela_str,
            operacoes_efetivado=1,
            operacoes_data_efetivado=datetime.combine(lanc.data, datetime.min.time()),
            operacoes_validacao=1,
            operacoes_fatura=fatura_id,
            operacoes_adicional_id=lanc.adicional_id,
            operacoes_grupo_id=grupo
        )
        self._db.add(saida)
        self._db.flush()
        id_saida = saida.operacoes_id

        # Lado da Entrada (Conta Destino)
        entrada = Operacao(
            operacoes_data_lancamento=lanc.data,
            operacoes_descricao=lanc.descricao,
            operacoes_conta=conta_destino,
            operacoes_valor=abs(lanc.valor),
            operacoes_tipo=4,
            operacoes_parcela=lanc.parcela_str,
            operacoes_efetivado=1,
            operacoes_data_efetivado=datetime.combine(lanc.data, datetime.min.time()),
            operacoes_validacao=1,
            operacoes_transf_rel=id_saida,
            operacoes_grupo_id=grupo
        )
        self._db.add(entrada)
        self._db.flush()
        id_entrada = entrada.operacoes_id

        # Vincula a saída à entrada
        saida.operacoes_transf_rel = id_entrada
        
        self._db.commit()
        log.info(f"  ↔ Transf #{id_saida}→#{id_entrada} R${lanc.valor:.2f}")
        return id_saida, id_entrada

    def buscar_ou_criar_fatura(self, conta_id: int, data_ref: date) -> Optional[int]:
        try:
            from app.routers.lancamentos import get_or_create_fatura
            return get_or_create_fatura(self._db, conta_id, data_ref)
        except ImportError:
            log.warning("get_or_create_fatura não encontrado")
            return None


# ═══════════════════════════════════════════════════════════════
# ORQUESTRADOR
# ═══════════════════════════════════════════════════════════════

class FaturaImporter:

    def __init__(self, db=None):
        self.db      = db
        self.matcher = FaturaMatcher()

    def processar(
        self, pdf_path: str | Path, conta_id: int, ano_override: Optional[int] = None
    ) -> tuple[list[ResultadoConciliacao], list[adicionalInfo]]:
        """Extrai + faz matching. Não grava nada no banco. Retorna (resultados, adicionais)."""
        log.info(f"📄 Processando: {pdf_path}")
        texto    = PDFExtractor.extrair_texto(pdf_path)
        ano      = ano_override or PDFExtractor.detectar_ano_fatura(texto)
        mes_fech = PDFExtractor.detectar_mes_fechamento(texto)
        log.info(f"   Ano: {ano} | Mês de fechamento: {mes_fech or '?'}")

        parser      = selecionar_parser(texto)
        lancamentos = parser.extrair_lancamentos(texto, ano, mes_fech)

        adicionais: list[adicionalInfo] = []
        if self.db:
            pr = adicionalRepository(self.db)
            adicionais = pr.detectar(lancamentos, conta_id)
            mapa = {p.cartao_final: p for p in adicionais}
            for l in lancamentos:
                pi = mapa.get(l.cartao_final)
                if pi and not pi.is_novo:
                    l.adicional_id = pi.adicional_id
                    if pi.conta_vinculada:
                        l.tipo_operacao = 4
                        l.conta_destino = pi.conta_vinculada

        if not self.db:
            return [ResultadoConciliacao(l, 0, "NOVO") for l in lancamentos], adicionais

        repo = FaturaRepository(self.db)
        resultados = []
        for l in lancamentos:
            cands     = repo.buscar_candidatos(l)
            melhor, s = self.matcher.melhor_match(l, cands)
            cls       = self.matcher.classificar(s)
            rc = ResultadoConciliacao(l, s, cls)
            if melhor:
                rc.operacao_id        = melhor.get("operacoes_id")
                rc.operacao_descricao = melhor.get("operacoes_descricao")
                rc.operacao_data      = melhor.get("operacoes_data_lancamento")
                rc.operacao_valor     = float(melhor.get("operacoes_valor") or 0)
            resultados.append(rc)

        novos  = sum(1 for r in resultados if r.classificacao == "NOVO")
        fortes = sum(1 for r in resultados if r.classificacao == "MATCH_FORTE")
        fracos = sum(1 for r in resultados if r.classificacao == "MATCH_FRACO")
        dups   = sum(1 for r in resultados if r.classificacao == "DUPLICIDADE")
        log.info(f"   Matching: {novos} novos | {fortes} forte | {fracos} fraco | {dups} dup")
        return resultados, adicionais

    def confirmar_adicionais(self, conta_id: int,
                             adicionais_confirmados: list[dict]) -> dict[str, int]:
        pr = adicionalRepository(self.db)
        mapa = {}
        for p in adicionais_confirmados:
            pid = pr.criar(
                conta_id=conta_id,
                adicional_nome=p["adicional_nome"],
                cartao_final=p["cartao_final"],
                apelido=p.get("apelido"),
                conta_vinculada=p.get("conta_vinculada"),
                titular=p.get("titular", False),
            )
            if pid:
                mapa[p["cartao_final"]] = pid
        return mapa


# ═══════════════════════════════════════════════════════════════
# CLI dry-run
# ═══════════════════════════════════════════════════════════════

def _main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--conta-id", type=int, required=True)
    ap.add_argument("--ano", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        importer = FaturaImporter(db=None)
        resultados, adicionais = importer.processar(args.pdf, args.conta_id, args.ano)
        grupos: dict[str, list] = {}
        for rc in resultados:
            l = rc.lancamento
            grupos.setdefault(f"{l.cartao_nome} ···{l.cartao_final}", []).append(rc)
        print(f"\n{'='*76}")
        print(f"  DRY RUN — {len(resultados)} lançamentos | {len(adicionais)} adicionais")
        print(f"{'='*76}")
        td = tc = 0.0
        for adicional, items in grupos.items():
            d = sum(r.lancamento.valor for r in items if r.lancamento.tipo == "D")
            c = sum(r.lancamento.valor for r in items if r.lancamento.tipo == "C")
            print(f"\n  ▶ {adicional}  ({len(items)})")
            print(f"  {'Data':<12} {'T':<3} {'Descrição':<42} {'Parc':<8} {'Valor':>10}")
            print(f"  {'─'*80}")
            for rc in items:
                l = rc.lancamento
                parc = f"{l.parcela_atual}/{l.parcela_total}" if l.parcela_atual else ""
                s = "-" if l.tipo == "D" else "+"
                print(f"  {str(l.data):<12} {l.tipo:<3} {l.descricao[:42]:<42} {parc:<8} {s}R${l.valor:>9.2f}")
            print(f"  {'─'*80}")
            print(f"  {'Débitos:':>60} -R${d:>9.2f}")
            if c: print(f"  {'Créditos:':>60} +R${c:>9.2f}")
            td += d; tc += c
        print(f"\n{'='*76}")
        print(f"  {'TOTAL Débitos:':>60} -R${td:>9.2f}")
        if tc: print(f"  {'TOTAL Créditos:':>60} +R${tc:>9.2f}")
        print(f"{'='*76}\n")


if __name__ == "__main__":
    _main()