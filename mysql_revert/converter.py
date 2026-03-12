import re

def converter_binlog_para_insert(arquivo_entrada, arquivo_saida):
    # Expressão regular para encontrar os valores @1, @2...
    regex_valor = re.compile(r"###\s+@\d+=(.*)")
    
    inserts = []
    valores_atuais = []

    with open(arquivo_entrada, 'r', encoding='utf-8') as f:
        for linha in f:
            # Identifica o início de um novo bloco de DELETE
            if "### DELETE FROM `prisma`.`operacoes`" in linha:
                if valores_atuais:
                    inserts.append(gerar_sql(valores_atuais))
                valores_atuais = []
                continue
            
            # Extrai o valor da linha (ex: @2='2025:04:26')
            match = regex_valor.search(linha)
            if match:
                valor = match.group(1).strip()
                
                # Correção de Data: Transforma '2025:04:26' em '2025-04-26'
                # O log do MySQL usa : em campos de data, o que causa erro no INSERT
                if re.match(r"'\d{4}:\d{2}:\d{2}'", valor):
                    valor = valor.replace(':', '-')
                
                valores_atuais.append(valor)

        # Adiciona o último registro processado
        if valores_atuais:
            inserts.append(gerar_sql(valores_atuais))

    # Salva o resultado
    with open(arquivo_saida, 'w', encoding='utf-8') as f:
        f.write("-- Script de Recuperacao Gerado\n")
        f.write("SET FOREIGN_KEY_CHECKS = 0; -- Desativa chaves estrangeiras para evitar erros de ordem\n\n")
        f.write("\n".join(inserts))
        f.write("\n\nSET FOREIGN_KEY_CHECKS = 1;")

    print(f"Sucesso! {len(inserts)} registros convertidos em {arquivo_saida}")

def gerar_sql(valores):
    # Une os valores separados por vírgula
    corpo_valores = ", ".join(valores)
    return f"INSERT INTO `operacoes` VALUES ({corpo_valores});"

if __name__ == "__main__":
    converter_binlog_para_insert('apenas_deletados.sql', 'recuperacao_final.sql')