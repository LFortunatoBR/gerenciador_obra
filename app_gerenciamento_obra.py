import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text
import unicodedata
from fpdf import FPDF
import os
from datetime import datetime
import pytz

def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')

st.set_page_config(page_title="Gestor de Obras", page_icon="🏗️", layout="wide") # Alterado para wide para caber a nova coluna

# ==========================================
# 1. TELA DE LOGIN
# ==========================================
def check_password():
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False
    if not st.session_state["autenticado"]:
        st.title("🔒 Acesso Restrito")
        senha_digitada = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            if senha_digitada == st.secrets["senha_app"]:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta!")
        return False
    return True

if not check_password():
    st.stop()

# ==========================================
# 2. CONEXÃO NEON E DADOS
# ==========================================
url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta)

st.title("🏗️ Planejador de Obras Integrado")

aba1, aba2, aba3 = st.tabs(["📊 Cronograma", "💰 Orçamento (SINAPI)", "⚙️ Gerenciar Tarefas"])

# Busca as tarefas uma vez no topo para uso geral
try:
    df_tarefas = conn.query("SELECT * FROM tarefas ORDER BY data_inicio;", ttl=0)
except Exception as e:
    st.error("Execute o comando SQL no Neon para criar a coluna dependencia_id.")
    st.stop()

# Dicionário de tarefas para menus suspensos
opcoes_dep = {0: "Nenhuma (Em paralelo)"}
tarefas_dict = {}
if not df_tarefas.empty:
    for _, r in df_tarefas.iterrows():
        nome_valido = r['nome_servico'] if pd.notna(r['nome_servico']) and r['nome_servico'] != "" else f"Tarefa s/ nome (ID: {r['id']})"
        opcoes_dep[r['id']] = nome_valido
        tarefas_dict[r['id']] = nome_valido

# --- ABA 1: CRONOGRAMA, TABELA E EXPORTAÇÃO PDF ---
with aba1:
    if not df_tarefas.empty:
        custo_total = df_tarefas['custo_previsto'].sum()
        st.metric(label="Custo Total Previsto da Obra", value=f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
        # Fuso Horário de Brasília/Rio
        fuso_brasil = pytz.timezone('America/Sao_Paulo')
        hoje = datetime.now(fuso_brasil).date()
        
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio']).dt.date
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim']).dt.date
        
        # ==========================================
        # PAINEL: ATUALIZAR PERCENTAGEM COM BLOQUEIOS RÍGIDOS
        # ==========================================
        with st.expander("📈 Gerir Avanço e Limpar Tarefas", expanded=True):
            col_sel, col_sld = st.columns(2)
            
            with col_sel:
                id_selecionado = st.selectbox("Selecione um serviço:", options=[k for k in tarefas_dict.keys()], format_func=lambda x: tarefas_dict[x])
            
            tarefa_selecionada = df_tarefas[df_tarefas['id'] == id_selecionado].iloc[0]
            perc_atual = int(tarefa_selecionada['conclusao_percentual'])
            data_inicio_tarefa = tarefa_selecionada['data_inicio']
            data_fim_tarefa = tarefa_selecionada['data_fim']
            
            with col_sld:
                novo_perc = st.slider("Alterar Conclusão (%)", 0, 100, perc_atual, key="slider_perc")
            
            # ------------------------------------------
            # MOTOR DE VALIDAÇÃO DE ENGENHARIA (BLOQUEIOS)
            # ------------------------------------------
            motivo_bloqueio = None
            
            # Regra 1: Atrasos (Apenas Aviso, não bloqueia de colocar em 100%)
            if hoje > data_fim_tarefa and perc_atual < 100:
                st.error(f"🚨 **EM ATRASO:** O prazo encerrou em {data_fim_tarefa.strftime('%d/%m/%Y')}.")
            
            # Regra 2: Bloqueio de Data Futura
            if novo_perc > 0 and hoje < data_inicio_tarefa:
                motivo_bloqueio = f"⛔ **Data Bloqueada:** A data de início é {data_inicio_tarefa.strftime('%d/%m/%Y')}. Você não pode relatar avanço antes do início da tarefa. Antecipe a data no banco se a obra adiantou."
                
            # Regra 3: Bloqueio de Predecessora (Dependência)
            dependencia_id = tarefa_selecionada['dependencia_id']
            if pd.notna(dependencia_id) and dependencia_id > 0:
                predecessora = df_tarefas[df_tarefas['id'] == dependencia_id]
                if not predecessora.empty:
                    perc_pred = predecessora.iloc[0]['conclusao_percentual']
                    nome_pred = predecessora.iloc[0]['nome_servico']
                    if perc_pred < 100 and novo_perc > 0:
                        motivo_bloqueio = f"⛔ **Dependência Bloqueada:** O serviço '{tarefa_selecionada['nome_servico']}' exige que '{nome_pred}' esteja 100% concluído. (Atual: {perc_pred}%)."
            
            # Se houver bloqueio, exibe erro e desativa o botão
            if motivo_bloqueio:
                st.error(motivo_bloqueio)
                
            col_btn_upd, col_btn_del, espaco = st.columns([1, 1, 2])
            with col_btn_upd:
                # O botão Gravar é desativado se houver motivo_bloqueio
                if st.button("Gravar Alteração", disabled=bool(motivo_bloqueio)):
                    with conn.session as s:
                        s.execute(text("UPDATE tarefas SET conclusao_percentual = :perc WHERE id = :id"), {"perc": novo_perc, "id": int(id_selecionado)})
                        s.commit()
                    st.success("Atualizado!")
                    st.rerun()

            with col_btn_del:
                if st.button("🗑️ Excluir Tarefa", type="primary"):
                    with conn.session as s:
                        s.execute(text("DELETE FROM tarefas WHERE id = :id"), {"id": int(id_selecionado)})
                        s.commit()
                    st.warning("Tarefa eliminada!")
                    st.rerun()
        # ==========================================
        
        df_tarefas['data_inicio_plot'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['data_fim_plot'] = pd.to_datetime(df_tarefas['data_fim'])

        fig = px.timeline(
            df_tarefas, x_start="data_inicio_plot", x_end="data_fim_plot", y="nome_servico", color="fase",
            hover_data=["conclusao_percentual", "custo_previsto"], title="Evolução da Obra"
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        # --- Formatação da Tabela ---
        # Mapeia o ID da dependência para o Nome para exibir na tabela
        df_tarefas['Nome Predecessora'] = df_tarefas['dependencia_id'].map(tarefas_dict).fillna("-")
        
        df_exibicao = df_tarefas[["nome_servico", "fase", "data_inicio", "data_fim", "Nome Predecessora", "custo_previsto", "conclusao_percentual"]].copy()
        df_exibicao.columns = ["Serviço", "Fase", "Início", "Término", "Depende de", "Custo Previsto", "Conclusão (%)"]
        
        df_exibicao['Início'] = pd.to_datetime(df_exibicao['Início']).dt.strftime('%d/%m/%Y')
        df_exibicao['Término'] = pd.to_datetime(df_exibicao['Término']).dt.strftime('%d/%m/%Y')
        
        nova_linha_total = pd.DataFrame([{"Serviço": "TOTAL DA OBRA", "Fase": "-", "Início": "-", "Término": "-", "Depende de": "-", "Custo Previsto": custo_total, "Conclusão (%)": "-"}])
        df_exibicao = pd.concat([df_exibicao, nova_linha_total], ignore_index=True)
        
        def formatar_moeda(valor):
            try:
                return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            except:
                return valor

        df_exibicao['Custo Previsto'] = df_exibicao['Custo Previsto'].apply(formatar_moeda)
        st.dataframe(df_exibicao, hide_index=True, use_container_width=True)
        
        # --- GERADOR DE PDF A4 ---
        st.divider()
        st.subheader("📄 Exportar Relatório Completo")
        if st.button("⚙️️ Processar Relatório em PDF"):
            with st.spinner("Desenhando gráfico e formatando folha A4..."):
                import matplotlib.pyplot as plt
                import matplotlib.dates as mdates
                import numpy as np

                fig_pdf, ax = plt.subplots(figsize=(10, 4), dpi=150)
                df_grafico = df_tarefas.copy().sort_values(by='data_inicio', ascending=False)
                cores_fases = {"Projetos": "#90caf9", "Preparação": "#1976d2", "Administrativo": "#eeeeee",
                               "Infraestrutura": "#ffcc80", "Superestrutura": "#ff9800", 
                               "Instalações": "#a5d6a7", "Acabamento": "#4caf50"}
                
                for idx, row in df_grafico.iterrows():
                    fase = str(row['fase'])
                    cor = cores_fases.get(fase, "#9e9e9e")
                    inicio = mdates.date2num(row['data_inicio_plot'])
                    fim = mdates.date2num(row['data_fim_plot'])
                    ax.barh(row['nome_servico'], fim - inicio, left=inicio, color=cor, edgecolor='black', alpha=0.8)

                ax.xaxis_date()
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))
                plt.xticks(rotation=45, ha='right', fontsize=8)
                plt.yticks(fontsize=8)
                plt.title("Cronograma Fisico da Obra", fontsize=12, pad=10)
                plt.tight_layout()
                
                caminho_imagem = "grafico_gantt_temp.png"
                plt.savefig(caminho_imagem)
                plt.close(fig_pdf)

                pdf = FPDF(orientation="L", unit="mm", format="A4") # Alterado para Paisagem (Landscape) para caber as colunas
                pdf.add_page()
                pdf.set_font("Arial", "B", 16)
                pdf.cell(270, 10, remover_acentos("Relatorio de Cronograma e Orcamento"), ln=True, align="C")
                
                pdf.set_font("Arial", "", 12)
                texto_custo = f"Custo Total Previsto: R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                pdf.cell(270, 10, remover_acentos(texto_custo), ln=True, align="C")
                pdf.ln(5)
                
                pdf.image(caminho_imagem, x=20, w=250)
                pdf.ln(5) 
                
                pdf.set_font("Arial", "B", 8)
                pdf.cell(75, 8, "Servico", 1)
                pdf.cell(20, 8, "Inicio", 1)
                pdf.cell(20, 8, "Termino", 1)
                pdf.cell(75, 8, "Depende de", 1)
                pdf.cell(30, 8, "Custo", 1)
                pdf.cell(20, 8, "Conclusao", 1, ln=True)
                
                pdf.set_font("Arial", "", 8)
                for index, row in df_exibicao.iterrows():
                    serv = remover_acentos(str(row['Serviço']))[:45]
                    ini = str(row['Início'])
                    fim = str(row['Término'])
                    dep = remover_acentos(str(row['Depende de']))[:45]
                    custo = remover_acentos(str(row['Custo Previsto']))
                    conc = str(row['Conclusão (%)'])
                    if conc != "-":
                        conc = f"{conc}%"
                    
                    pdf.cell(75, 8, serv, 1)
                    pdf.cell(20, 8, ini, 1)
                    pdf.cell(20, 8, fim, 1)
                    pdf.cell(75, 8, dep, 1)
                    pdf.cell(30, 8, custo, 1)
                    pdf.cell(20, 8, conc, 1, ln=True)
                
                pdf.output("relatorio_obra.pdf")
                with open("relatorio_obra.pdf", "rb") as f:
                    st.session_state['pdf_pronto'] = f.read()
                try:
                    os.remove(caminho_imagem)
                except:
                    pass

        if 'pdf_pronto' in st.session_state:
            st.success("Relatório gerado!")
            st.download_button(label="⬇️ Baixar PDF A4", data=st.session_state['pdf_pronto'], file_name="Cronograma.pdf", mime="application/pdf")
            
    else:
        st.info("Nenhuma tarefa cadastrada.")

# --- ABA 2: BUSCADOR COM ADIÇÃO DIRETA AO CRONOGRAMA ---
with aba2:
    st.subheader("Adicionar Serviços do SINAPI")
    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar (ex: Alvenaria, Concreto, Porcelanato)")
    
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        
        query = f"SELECT codigo, descricao, unidade, preco_mediano FROM {tabela_alvo} WHERE descricao ILIKE '%{busca}%' LIMIT 20;"
        df_sinapi = conn.query(query, ttl=600)
        
        if not df_sinapi.empty:
            for index, row in df_sinapi.iterrows():
                with st.expander(f"📦 {row['descricao'][:60]}... | R$ {float(row['preco_mediano']):.2f} / {row['unidade']}"):
                    st.write(f"**Descrição Completa:** {row['descricao']}")
                    
                    with st.form(f"add_direto_{index}"):
                        col1, col2 = st.columns(2)
                        quantidade = col1.number_input(f"Quantidade ({row['unidade']})", min_value=0.1, value=1.0, step=1.0, format="%.2f")
                        fase_escolhida = col2.selectbox("Fase", ["Projetos", "Serviços Preliminares", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
                        
                        col3, col4 = st.columns(2)
                        data_inicio = col3.date_input("Início do Serviço")
                        data_fim = col4.date_input("Fim do Serviço")
                        
                        nome_abreviado = st.text_input("Nome Resumido para o Gráfico", value=row['descricao'][:50].title())
                        
                        # NOVO: Seleção de Dependência
                        dep_id_escolhida = st.selectbox("Depende do término de qual tarefa?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
                        
                        btn_salvar = st.form_submit_button("➕ Adicionar Serviço")
                        
                        if btn_salvar:
                            if data_inicio > data_fim:
                                st.error("Data de início não pode ser maior que o término!")
                            else:
                                custo_total_servico = float(row['preco_mediano']) * quantidade
                                val_dep = None if dep_id_escolhida == 0 else dep_id_escolhida
                                with conn.session as s:
                                    sql = text("""
                                        INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id) 
                                        VALUES (:nome, :fase, :inicio, :fim, 0, :custo, :dep)
                                    """)
                                    s.execute(sql, {"nome": nome_abreviado, "fase": fase_escolhida, "inicio": data_inicio, "fim": data_fim, "custo": custo_total_servico, "dep": val_dep})
                                    s.commit()
                                st.success("Serviço adicionado!")
                                st.rerun()
        else:
            st.warning("Nenhum item encontrado.")

# --- ABA 3: ADICIONAR TAREFAS MANUAIS (SEM SINAPI) ---
with aba3:
    st.subheader("Adicionar Tarefa Manual")
    with st.form("form_nova_tarefa_manual"):
        nome = st.text_input("Nome da Tarefa")
        fase = st.selectbox("Fase", ["Projetos", "Administrativo", "Serviços Preliminares", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
        
        col1, col2 = st.columns(2)
        inicio = col1.date_input("Início")
        fim = col2.date_input("Término")
        
        custo = st.number_input("Custo Previsto Global (R$)", min_value=0.0, format="%.2f")
        conclusao = st.slider("Conclusão Atual (%)", 0, 100, 0)
        
        # NOVO: Seleção de Dependência
        dep_id_escolhida = st.selectbox("Depende do término de qual tarefa?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
        
        submit = st.form_submit_button("Salvar Tarefa Manual")
        
        if submit:
            if inicio > fim:
                st.error("A data de início não pode ser maior que o término!")
            else:
                val_dep = None if dep_id_escolhida == 0 else dep_id_escolhida
                with conn.session as s:
                    sql = text("""
                        INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id) 
                        VALUES (:nome, :fase, :inicio, :fim, :conc, :custo, :dep)
                    """)
                    s.execute(sql, {"nome": nome, "fase": fase, "inicio": inicio, "fim": fim, "conc": conclusao, "custo": custo, "dep": val_dep})
                    s.commit()
                st.success("Tarefa manual salva!")
                st.rerun()

    st.divider()
    if st.button("Fazer Logout"):
        st.session_state["autenticado"] = False
        st.rerun()
