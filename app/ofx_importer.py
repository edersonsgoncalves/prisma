"""
ofx_importer.py
===============
Leitura, interpretação e inserção de dados de arquivos OFX no MySQL/MariaDB.

Funcionalidades:
  - Parse de OFX nos formatos SGML (legado) e XML (moderno)
  - Normalização de dados entre diferentes bancos emissores
  - Matching inteligente com registros existentes no banco (score ponderado)
  - Detecção de duplicidades por FITID
  - Fluxo de confirmação interativa para matches ambíguos
  - Efetivação das transações confirmadas

Dependências:
  pip install mysql-connector-python python-dateutil difflib

Estrutura esperada da tabela `operacoes`:
  CREATE TABLE operacoes (
      id                      INT AUTO_INCREMENT PRIMARY KEY,
      descricao               VARCHAR(255),
      valor                   DECIMAL(15,2),
      data_lancamento         DATE,
      tipo                    CHAR(1),          -- 'D' débito / 'C' crédito
      categoria               VARCHAR(100),
      conta_id                INT,
      ofx_fitid               VARCHAR(255),     -- ID único do lançamento no OFX
      ofx_memo                VARCHAR(500),
      operacoes_efetivado     TINYINT DEFAULT 0,
      operacoes_data_efetivado DATE,
      criado_em               DATETIME DEFAULT CURRENT_TIMESTAMP
  );
"""

import re
import sys
import logging
from datetime import datetime, date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:
    sys.exit("❌  Instale: pip install mysql-connector-python")

try:
    from dateutil import parser as dateutil_parser
except ImportError:
    sys.exit("❌  Instale: pip install python-dateutil")


# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("ofx_importer")


# ---------------------------------------------------------------------------
# Configuração de banco de dados
# Edite as variáveis abaixo ou passe um dict ao instanciar OFXImporter
# ---------------------------------------------------------------------------
DEFAULT_DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "senha",
    "database": "financeiro",
    "charset": "utf8mb4",
}


# ---------------------------------------------------------------------------
# Pesos e limiares de matching
# ---------------------------------------------------------------------------
SCORE_FITID_MATCH = 100   # FITID idêntico → duplicidade certa
SCORE_VALOR_EXATO = 40
SCORE_DATA_EXATA = 30
SCORE_DATA_1DIA = 20
SCORE_DATA_3DIAS = 10
SCORE_MEMO_80 = 20        # similaridade de texto ≥ 80 %
SCORE_TIPO_IGUAL = 10

THRESHOLD_DUPLICIDADE = 100   # score ≥ este valor → DUPLICIDADE
THRESHOLD_FORTE = 70          # score ≥ este → match forte (pede confirmação)
THRESHOLD_FRACO = 40          # score ≥ este → match fraco (sugere, permite ignorar)


# ===========================================================================
# 1. PARSER OFX
# ===========================================================================

class OFXParser:
    """
    Suporta dois formatos:
      - SGML/OFX 1.x  (cabeçalho de texto + tags sem fechamento)
      - XML/OFX 2.x   (XML bem-formado)
    """

    # Mapeamento de tipo OFX → D/C
    TIPO_MAP = {
        "DEBIT": "D", "CREDIT": "C", "INT": "C", "DIV": "C",
        "FEE": "D", "SRVCHG": "D", "DEP": "C", "ATM": "D",
        "POS": "D", "XFER": "D", "CHECK": "D", "PAYMENT": "D",
        "CASH": "D", "DIRECTDEP": "C", "DIRECTDEBIT": "D",
        "REPEATPMT": "D", "OTHER": "D",
    }

    def parse(self, filepath: str) -> list[dict]:
        """Lê o arquivo e retorna lista de transações normalizadas."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {filepath}")

        raw = path.read_text(encoding="utf-8", errors="replace")

        if self._is_xml(raw):
            log.info("Formato detectado: OFX/XML 2.x")
            transacoes = self._parse_xml(raw)
        else:
            log.info("Formato detectado: OFX/SGML 1.x")
            transacoes = self._parse_sgml(raw)

        log.info(f"  → {len(transacoes)} transações encontradas no arquivo")
        return transacoes

    # ------------------------------------------------------------------
    # Detecção de formato
    # ------------------------------------------------------------------
    def _is_xml(self, raw: str) -> bool:
        return bool(re.search(r"<\?xml|<OFX>|<BANKTRANLIST>", raw, re.IGNORECASE))

    # ------------------------------------------------------------------
    # Parser SGML (OFX 1.x)
    # ------------------------------------------------------------------
    def _parse_sgml(self, raw: str) -> list[dict]:
        """
        OFX SGML não é XML válido: tags sem fechamento, sem aspas nos atributos.
        Estratégia: extrair blocos <STMTTRN>...</STMTTRN> com regex.
        """
        # Normaliza quebras de linha e remove cabeçalho antes de <OFX>
        body = re.sub(r"^.*?<OFX>", "<OFX>", raw, flags=re.DOTALL | re.IGNORECASE)

        transacoes = []
        blocos = re.findall(
            r"<STMTTRN>(.*?)</STMTTRN>", body, re.DOTALL | re.IGNORECASE
        )

        for bloco in blocos:
            t = self._extrair_campos_sgml(bloco)
            if t:
                transacoes.append(t)

        return transacoes

    def _extrair_campos_sgml(self, bloco: str) -> Optional[dict]:
        def campo(tag: str) -> str:
            m = re.search(rf"<{tag}>\s*([^\r\n<]*)", bloco, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        fitid = campo("FITID")
        if not fitid:
            return None

        tipo_ofx = campo("TRNTYPE").upper()
        valor_str = campo("TRNAMT").replace(",", ".")
        data_str = campo("DTPOSTED") or campo("DTUSER")
        memo = campo("MEMO") or campo("NAME") or ""

        return self._normalizar(fitid, tipo_ofx, valor_str, data_str, memo)

    # ------------------------------------------------------------------
    # Parser XML (OFX 2.x)
    # ------------------------------------------------------------------
    def _parse_xml(self, raw: str) -> list[dict]:
        """
        OFX 2.x é XML válido. Usamos regex em vez de xml.etree para
        maior tolerância a variações entre bancos.
        """
        transacoes = []
        blocos = re.findall(
            r"<STMTTRN>(.*?)</STMTTRN>", raw, re.DOTALL | re.IGNORECASE
        )

        for bloco in blocos:
            t = self._extrair_campos_xml(bloco)
            if t:
                transacoes.append(t)

        return transacoes

    def _extrair_campos_xml(self, bloco: str) -> Optional[dict]:
        def campo(tag: str) -> str:
            m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", bloco, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        fitid = campo("FITID")
        if not fitid:
            return None

        tipo_ofx = campo("TRNTYPE").upper()
        valor_str = campo("TRNAMT").replace(",", ".")
        data_str = campo("DTPOSTED") or campo("DTUSER")
        memo = campo("MEMO") or campo("NAME") or ""

        return self._normalizar(fitid, tipo_ofx, valor_str, data_str, memo)

    # ------------------------------------------------------------------
    # Normalização comum
    # ------------------------------------------------------------------
    def _normalizar(
        self, fitid: str, tipo_ofx: str, valor_str: str, data_str: str, memo: str
    ) -> Optional[dict]:
        try:
            valor = float(valor_str)
        except ValueError:
            log.warning(f"Valor inválido para FITID {fitid}: '{valor_str}' — ignorado")
            return None

        data = self._parse_data(data_str)
        if not data:
            log.warning(f"Data inválida para FITID {fitid}: '{data_str}' — ignorado")
            return None

        # Tipo baseado no sinal do valor (mais confiável que TRNTYPE em alguns bancos)
        if valor < 0:
            tipo = "D"
            valor = abs(valor)
        else:
            tipo = self.TIPO_MAP.get(tipo_ofx, "C" if valor >= 0 else "D")

        return {
            "fitid": fitid,
            "tipo": tipo,
            "valor": round(valor, 2),
            "data": data,
            "memo": self._limpar_memo(memo),
        }

    def _parse_data(self, data_str: str) -> Optional[date]:
        """Aceita formatos: YYYYMMDD, YYYYMMDDHHMMSS, YYYYMMDDHHMMSS.000[-05:00], etc."""
        if not data_str:
            return None
        # Remove timezone e milissegundos, mantém só os primeiros 14 chars numéricos
        nums = re.sub(r"[^\d]", "", data_str)[:14]
        try:
            if len(nums) >= 8:
                return datetime.strptime(nums[:8], "%Y%m%d").date()
        except ValueError:
            pass
        try:
            return dateutil_parser.parse(data_str).date()
        except Exception:
            return None

    def _limpar_memo(self, memo: str) -> str:
        """Remove espaços duplos e caracteres de controle."""
        memo = re.sub(r"[\x00-\x1f]", " ", memo)
        return re.sub(r"\s+", " ", memo).strip()[:500]


# ===========================================================================
# 2. MATCHER
# ===========================================================================

class TransacaoMatcher:
    """
    Compara cada transação do OFX contra os registros do banco de dados.
    Retorna o melhor match e seu score.
    """

    def calcular_score(self, ofx: dict, db: dict) -> int:
        score = 0

        # FITID idêntico → duplicidade imediata
        if ofx.get("fitid") and db.get("ofx_fitid") == ofx["fitid"]:
            return SCORE_FITID_MATCH

        # Valor
        if abs(float(db["valor"]) - ofx["valor"]) < 0.01:
            score += SCORE_VALOR_EXATO

        # Data
        diff_dias = abs((db["data_lancamento"] - ofx["data"]).days)
        if diff_dias == 0:
            score += SCORE_DATA_EXATA
        elif diff_dias == 1:
            score += SCORE_DATA_1DIA
        elif diff_dias <= 3:
            score += SCORE_DATA_3DIAS

        # Tipo (D/C)
        if db.get("tipo") == ofx.get("tipo"):
            score += SCORE_TIPO_IGUAL

        # Similaridade de texto
        memo_ofx = ofx.get("memo", "").lower()
        memo_db = (db.get("ofx_memo") or db.get("descricao") or "").lower()
        if memo_ofx and memo_db:
            ratio = SequenceMatcher(None, memo_ofx, memo_db).ratio()
            if ratio >= 0.80:
                score += SCORE_MEMO_80

        return score

    def encontrar_melhor_match(self, ofx: dict, candidatos: list[dict]) -> tuple[Optional[dict], int]:
        melhor = None
        melhor_score = 0
        for c in candidatos:
            s = self.calcular_score(ofx, c)
            if s > melhor_score:
                melhor_score = s
                melhor = c
        return melhor, melhor_score

    def classificar(self, score: int) -> str:
        if score >= THRESHOLD_DUPLICIDADE:
            return "DUPLICIDADE"
        if score >= THRESHOLD_FORTE:
            return "MATCH_FORTE"
        if score >= THRESHOLD_FRACO:
            return "MATCH_FRACO"
        return "NOVO"


# ===========================================================================
# 3. REPOSITÓRIO (acesso ao banco de dados)
# ===========================================================================

class OperacoesRepository:
    """Encapsula todas as queries ao banco de dados."""

    def __init__(self, conn):
        self._conn = conn

    def buscar_candidatos(self, ofx: dict, janela_dias: int = 10) -> list[dict]:
        """
        Busca registros no banco próximos à transação do OFX.
        Filtra por valor exato E data dentro de ±janela_dias.
        Evita trazer tudo para a memória.
        """
        data_min = ofx["data"] - timedelta(days=janela_dias)
        data_max = ofx["data"] + timedelta(days=janela_dias)

        sql = """
            SELECT
                id, descricao, valor, data_lancamento, tipo,
                ofx_fitid, ofx_memo,
                operacoes_efetivado, operacoes_data_efetivado
            FROM operacoes
            WHERE valor = %s
              AND data_lancamento BETWEEN %s AND %s
        """
        cursor = self._conn.cursor(dictionary=True)
        cursor.execute(sql, (ofx["valor"], data_min, data_max))
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def buscar_por_fitid(self, fitid: str) -> Optional[dict]:
        sql = "SELECT * FROM operacoes WHERE ofx_fitid = %s LIMIT 1"
        cursor = self._conn.cursor(dictionary=True)
        cursor.execute(sql, (fitid,))
        row = cursor.fetchone()
        cursor.close()
        return row

    def efetivar(self, operacao_id: int, data_efetivado: date, fitid: str, memo: str):
        sql = """
            UPDATE operacoes
               SET operacoes_efetivado       = 1,
                   operacoes_data_efetivado  = %s,
                   ofx_fitid                 = %s,
                   ofx_memo                  = %s
             WHERE id = %s
        """
        cursor = self._conn.cursor()
        cursor.execute(sql, (data_efetivado, fitid, memo, operacao_id))
        self._conn.commit()
        cursor.close()
        log.info(f"  ✅  Operação ID {operacao_id} efetivada.")

    def inserir(self, ofx: dict) -> int:
        sql = """
            INSERT INTO operacoes
                (descricao, valor, data_lancamento, tipo,
                 ofx_fitid, ofx_memo,
                 operacoes_efetivado, operacoes_data_efetivado)
            VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
        """
        cursor = self._conn.cursor()
        cursor.execute(sql, (
            ofx["memo"], ofx["valor"], ofx["data"], ofx["tipo"],
            ofx["fitid"], ofx["memo"], ofx["data"],
        ))
        self._conn.commit()
        novo_id = cursor.lastrowid
        cursor.close()
        log.info(f"  ➕  Nova operação inserida com ID {novo_id}.")
        return novo_id


# ===========================================================================
# 4. INTERFACE DE CONFIRMAÇÃO (terminal)
# ===========================================================================

class ConfirmacaoUI:
    """
    Exibe comparação lado a lado e coleta decisão do usuário.
    Pode ser substituída por uma interface web/gráfica sem alterar
    a lógica principal do importador.
    """

    SEP = "─" * 72

    def exibir_comparacao(self, ofx: dict, db: dict, score: int, classificacao: str):
        print(f"\n{self.SEP}")
        label = {
            "DUPLICIDADE": "⚠️  POSSÍVEL DUPLICIDADE",
            "MATCH_FORTE": "🔍 MATCH FORTE",
            "MATCH_FRACO": "💡 MATCH FRACO",
        }.get(classificacao, "")
        print(f"  {label}  (score: {score})")
        print(self.SEP)
        print(f"  {'Campo':<25} {'ARQUIVO OFX':<30} {'BANCO DE DADOS'}")
        print(f"  {'─'*25} {'─'*30} {'─'*20}")

        def linha(campo, v_ofx, v_db):
            print(f"  {campo:<25} {str(v_ofx):<30} {str(v_db)}")

        linha("FITID",         ofx.get("fitid", "-"),    db.get("ofx_fitid", "-"))
        linha("Data",          ofx["data"],               db["data_lancamento"])
        linha("Valor",         f"R$ {ofx['valor']:.2f}",  f"R$ {float(db['valor']):.2f}")
        linha("Tipo",          ofx["tipo"],               db.get("tipo", "-"))
        linha("Memo/Descr.",   ofx["memo"][:28],          (db.get("ofx_memo") or db.get("descricao", ""))[:28])
        linha("Efetivado",     "-",                       "Sim" if db.get("operacoes_efetivado") else "Não")
        linha("ID no banco",   "-",                       db["id"])
        print(self.SEP)

    def perguntar(self, opcoes: list[str]) -> str:
        while True:
            resp = input("  Opção: ").strip().upper()
            if resp in [o.upper() for o in opcoes]:
                return resp
            print(f"  ❌ Resposta inválida. Opções: {', '.join(opcoes)}")

    def solicitar_confirmacao_match(self, ofx: dict, db: dict, score: int, classificacao: str) -> str:
        """
        Retorna:
          'C'  → Confirmar (efetivar este par)
          'I'  → Ignorar / pular
          'N'  → Inserir como novo (ignorar match)
        """
        self.exibir_comparacao(ofx, db, score, classificacao)
        print("  [C] Confirmar match e efetivar")
        print("  [I] Ignorar este lançamento (pular)")
        print("  [N] Inserir como novo registro")
        return self.perguntar(["C", "I", "N"])

    def solicitar_decisao_duplicidade(self, ofx: dict, db: dict) -> str:
        """
        Retorna:
          'I'  → Ignorar (já existe)
          'F'  → Forçar re-efetivação
        """
        self.exibir_comparacao(ofx, db, SCORE_FITID_MATCH, "DUPLICIDADE")
        print("  Este lançamento já existe no banco (mesmo FITID).")
        print("  [I] Ignorar (recomendado)")
        print("  [F] Forçar atualização mesmo assim")
        return self.perguntar(["I", "F"])


# ===========================================================================
# 5. IMPORTADOR PRINCIPAL
# ===========================================================================

class OFXImporter:
    """
    Orquestra o fluxo completo:
      parse → match → confirmação → efetivação
    """

    def __init__(self, db_config: dict = None, conta_id: int = None):
        self._db_config = db_config or DEFAULT_DB_CONFIG
        self._conta_id = conta_id
        self._parser = OFXParser()
        self._matcher = TransacaoMatcher()
        self._ui = ConfirmacaoUI()
        self._conn = None
        self._repo = None

    # ------------------------------------------------------------------
    # Conexão
    # ------------------------------------------------------------------
    def _conectar(self):
        try:
            self._conn = mysql.connector.connect(**self._db_config)
            self._repo = OperacoesRepository(self._conn)
            log.info("Conectado ao banco de dados.")
        except MySQLError as e:
            sys.exit(f"❌  Erro de conexão: {e}")

    def _desconectar(self):
        if self._conn and self._conn.is_connected():
            self._conn.close()
            log.info("Conexão encerrada.")

    # ------------------------------------------------------------------
    # Ponto de entrada
    # ------------------------------------------------------------------
    def importar(self, filepath: str):
        log.info(f"Iniciando importação: {filepath}")
        self._conectar()

        try:
            transacoes = self._parser.parse(filepath)
            self._processar_transacoes(transacoes)
        finally:
            self._desconectar()

    # ------------------------------------------------------------------
    # Loop de processamento
    # ------------------------------------------------------------------
    def _processar_transacoes(self, transacoes: list[dict]):
        resumo = {"efetivados": 0, "inseridos": 0, "ignorados": 0, "duplicidades": 0}

        total = len(transacoes)
        for idx, ofx in enumerate(transacoes, 1):
            print(f"\n[{idx}/{total}] FITID: {ofx['fitid']}  |  {ofx['data']}  |  R$ {ofx['valor']:.2f}  |  {ofx['tipo']}  |  {ofx['memo'][:40]}")

            resultado = self._processar_uma(ofx)
            resumo[resultado] = resumo.get(resultado, 0) + 1

        # Resumo final
        print(f"\n{'═'*72}")
        print(f"  IMPORTAÇÃO CONCLUÍDA")
        print(f"  Efetivados  : {resumo['efetivados']}")
        print(f"  Inseridos   : {resumo['inseridos']}")
        print(f"  Duplicidades: {resumo['duplicidades']}")
        print(f"  Ignorados   : {resumo['ignorados']}")
        print(f"{'═'*72}\n")

    def _processar_uma(self, ofx: dict) -> str:
        """
        Processa uma transação do OFX.
        Retorna o resultado: 'efetivados', 'inseridos', 'ignorados', 'duplicidades'.
        """
        # 1. Verifica FITID primeiro (duplicidade certa)
        existente = self._repo.buscar_por_fitid(ofx["fitid"])
        if existente:
            return self._tratar_duplicidade(ofx, existente)

        # 2. Busca candidatos por valor + data
        candidatos = self._repo.buscar_candidatos(ofx)

        if not candidatos:
            return self._tratar_sem_match(ofx)

        melhor, score = self._matcher.encontrar_melhor_match(ofx, candidatos)
        classificacao = self._matcher.classificar(score)

        if classificacao == "NOVO":
            return self._tratar_sem_match(ofx)

        # 3. Apresenta match ao usuário
        decisao = self._ui.solicitar_confirmacao_match(ofx, melhor, score, classificacao)

        if decisao == "C":
            self._repo.efetivar(melhor["id"], ofx["data"], ofx["fitid"], ofx["memo"])
            return "efetivados"
        elif decisao == "N":
            self._repo.inserir(ofx)
            return "inseridos"
        else:
            print("  ⏭️  Lançamento ignorado pelo usuário.")
            return "ignorados"

    def _tratar_duplicidade(self, ofx: dict, existente: dict) -> str:
        decisao = self._ui.solicitar_decisao_duplicidade(ofx, existente)
        if decisao == "F":
            self._repo.efetivar(existente["id"], ofx["data"], ofx["fitid"], ofx["memo"])
            return "efetivados"
        print("  ⏭️  Duplicidade ignorada.")
        return "duplicidades"

    def _tratar_sem_match(self, ofx: dict) -> str:
        print(f"  Nenhum match encontrado no banco.")
        print(f"  [I] Inserir como novo  |  [P] Pular")
        resp = self._ui.perguntar(["I", "P"])
        if resp == "I":
            self._repo.inserir(ofx)
            return "inseridos"
        print("  ⏭️  Lançamento pulado.")
        return "ignorados"


# ===========================================================================
# 6. PONTO DE ENTRADA (CLI)
# ===========================================================================

def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Importa arquivo OFX para o banco MySQL/MariaDB"
    )
    ap.add_argument("arquivo", help="Caminho para o arquivo .ofx")
    ap.add_argument("--host",     default=DEFAULT_DB_CONFIG["host"])
    ap.add_argument("--port",     default=DEFAULT_DB_CONFIG["port"], type=int)
    ap.add_argument("--user",     default=DEFAULT_DB_CONFIG["user"])
    ap.add_argument("--password", default=DEFAULT_DB_CONFIG["password"])
    ap.add_argument("--database", default=DEFAULT_DB_CONFIG["database"])
    ap.add_argument("--conta-id", default=None, type=int,
                    help="ID da conta no banco (opcional, para filtrar candidatos)")
    args = ap.parse_args()

    db_config = {
        "host":     args.host,
        "port":     args.port,
        "user":     args.user,
        "password": args.password,
        "database": args.database,
        "charset":  "utf8mb4",
    }

    importer = OFXImporter(db_config=db_config, conta_id=args.conta_id)
    importer.importar(args.arquivo)


if __name__ == "__main__":
    main()
