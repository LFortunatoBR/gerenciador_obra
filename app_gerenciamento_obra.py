import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text
import unicodedata
from fpdf import FPDF
import os
from datetime import datetime

# Função para evitar erros de acentos no PDF
def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')

st.set_page_config(page_title="Gestor de Obras", page_icon="🏗️", layout="centered")

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
# 2. CONEXÃO NEON
# ==========================================
url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta)

st.title("🏗️ Planejador de Obras Integrado")

aba1, aba2, aba3 = st.tabs(["📊 Cronograma", "💰 Orçamento (SINAPI)", "⚙️ Gerenciar Tarefas"])

# --- ABA 1: CRONOGRAMA, TABELA E EXPORTAÇÃO PDF ---
with aba1:
    df_tarefas = conn.query("SELECT * FROM tarefas ORDER BY data_inicio;", ttl=0)
    
    if not df_tarefas.empty:
        custo_total = df_tarefas['custo_previsto'].sum()
        st.metric(label="Custo Total Previsto da Obra", value=f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
        # Converte as datas logo no início para podermos usá-las nas validações
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio']).dt.date
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim']).dt.date
        hoje = datetime.now().date()
        
        # ==========================================
        # PAINEL: ATUALIZAR PERCENTAGEM E EXCLUIR (COM AVISOS DE DATA)
        # ==========================================
        with st.expander("📈 Gerir Avanço e Limpar Tarefas", expanded=True):
            st.write("Selecione um serviço para atualizar a percentagem real ou excluí-lo do cronograma:")
            col_sel, col_sld, col_btn_upd, col_btn_del = st.columns([2, 2, 1, 1])
            
            with col_sel:
                tarefas_dict = {row['id']: (row['nome_servico'] if pd.notna(row['nome_servico']) and row['nome_servico'] != "" else f"Tarefa sem nome (ID: {row['id']})") for _, row in df_tarefas.iterrows()}
                id_selecionado = st.selectbox("Serviço", options=list(tarefas_dict.keys()), format_func=lambda x: tarefas_dict[x])
            
            # Dados da tarefa selecionada
            tarefa_selecionada = df_tarefas[df_tarefas['id'] == id_selecionado].iloc[0]
            perc_atual = int(tarefa_selecionada['conclusao_percentual'])
            data_inicio_tarefa = tarefa_selecionada['data_inicio']
            data_fim_tarefa = tarefa_selecionada['data_fim']
            
            # Lógica de Avisos e Cores
            if hoje < data_inicio_tarefa:
                st.info(f"⏳ **Atenção:** Esta etapa ainda não começou. O início está previsto para {data_inicio_tarefa.strftime('%d/%m/%Y')}.")
            elif hoje > data_fim_tarefa and perc_atual < 100:
                st.error(f"🚨 **EM ATRASO:** O prazo terminou a {data_fim_tarefa.strftime('%d/%m/%Y')}. Atualize a percentagem ou reveja o cronograma.")
            elif data_inicio_tarefa <= hoje <= data_fim_tarefa:
                st.success("✅ Esta tarefa está dentro do período de execução.")

            with col_sld:
                novo_perc = st.slider("Conclusão (%)", 0, 100, perc_atual, key="slider_perc")
            
            with col_btn_upd:
                st.write("") 
                st.write("")
                if st.button("Gravar Alteração"):
                    with conn.session as s:
                        s.execute(text("UPDATE tarefas SET conclusao_percentual = :perc WHERE id = :id"), {"perc": novo_perc, "id": int(id_selecionado)})
                        s.commit()
                    st.success("Atualizado!")
                    st.rerun()

            with col_btn_del:
                st.write("") 
                st.write("")
                if st.button("🗑️ Excluir", type="primary"):
                    with conn.session as s:
                        s.execute(text("DELETE FROM tarefas WHERE id = :id"), {"id": int(id_selecionado)})
                        s.commit()
                    st.error("Tarefa eliminada!")
                    st.rerun()
        # ==========================================
        
        # Prepara dados para o gráfico
        df_tarefas['data_inicio_plot'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['data_fim_plot'] = pd.to_datetime(df_tarefas['data_fim'])

        fig = px.timeline(
            df_tarefas, x_start="data_inicio_plot", x_end="data_fim_plot", y="nome_servico", color="fase",
            hover_data=["conclusao_percentual", "custo_previsto"], title="Evolução da Obra"
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        # --- Formatação da Tabela para Exibição ---
        df_exibicao = df_tarefas[["nome_servico", "fase", "data_inicio", "data_fim", "custo_previsto", "conclusao_percentual"]].copy()
        df_exibicao.columns = ["Serviço", "Fase", "Início", "Término", "Custo Previsto", "Conclusão (%)"]
        
        df_exibicao['Início'] = pd.to_datetime(df_exibicao['Início']).dt.strftime('%d/%m/%Y')
        df_exibicao['Término'] = pd.to_datetime(df_exibicao['Término']).dt.strftime('%d/%m/%Y')
        
        nova_linha_total = pd.DataFrame([{"Serviço": "TOTAL DA OBRA", "Fase": "-", "Início": "-", "Término": "-", "Custo Previsto": custo_total, "Conclusão (%)": "-"}])
        df_exibicao = pd.concat([df_exibicao, nova_linha_total], ignore_index=True)
        
        def formatar_moeda(valor):
            try:
                return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            except:
                return valor

        df_exibicao['Custo Previsto'] = df_exibicao['Custo Previsto'].apply(formatar_moeda)
        st.dataframe(df_exibicao, hide_index=True, use_container_width=True)
        
        # --- GERADOR DE PDF A4 (COM GRÁFICO SEGURO) ---
        st.divider()
        st.subheader("📄 Exportar Relatório Completo")
        
        if st.button("⚙️ Processar Relatório em PDF"):
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

                pdf = FPDF(orientation="P", unit="mm", format="A4")
                pdf.add_page()
                
                pdf.set_font("Arial", "B", 16)
                pdf.cell(190, 10, remover_acentos("Relatorio de Cronograma e Orcamento da Obra"), ln=True, align="C")
                
                pdf.set_font("Arial", "", 12)
                texto_custo = f"Custo Total Previsto: R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                pdf.cell(190, 10, remover_acentos(texto_custo), ln=True, align="C")
                pdf.ln(5)
                
                pdf.image(caminho_imagem, x=10, w=190)
                pdf.ln(5) 
                
                pdf.set_font("Arial", "B", 9)
                pdf.cell(70, 8, "Servico", 1)
                pdf.cell(25, 8, "Inicio", 1)
                pdf.cell(25, 8, "Termino", 1)
                pdf.cell(40, 8, "Custo Previsto", 1)
                pdf.cell(30, 8, "Conclusao", 1, ln=True)
                
                pdf.set_font("Arial", "", 8)
                for index, row in df_exibicao.iterrows():
                    serv = remover_acentos(str(row['Serviço']))[:35]
                    ini = str(row['Início'])
                    fim = str(row['Término'])
                    custo = remover_acentos(str(row['Custo Previsto']))
                    conc = str(row['Conclusão (%)'])
                    if conc != "-":
                        conc = f"{conc}%"
                    
                    pdf.cell(70, 8, serv, 1)
                    pdf.cell(25, 8, ini, 1)
                    pdf.cell(25, 8, fim, 1)
                    pdf.cell(40, 8, custo, 1)
                    pdf.cell(30, 8, conc, 1, ln=True)
                
                pdf.output("relatorio_obra.pdf")
                with open("relatorio_obra.pdf", "rb") as f:
                    st.session_state['pdf_pronto'] = f.read()
                
                try:
                    os.remove(caminho_imagem)
                except:
                    pass

        if 'pdf_pronto' in st.session_state:
            st.success("Relatório com gráfico gerado com sucesso!")
            st.download_button(
                label="⬇️ Baixar PDF A4",
                data=st.session_state['pdf_pronto'],
                file_name="Cronograma_Gestor_Obras.pdf",
                mime="application/pdf"
            )
            
    else:
        st.info("Nenhuma tarefa cadastrada.")

# --- ABA 2: BUSCADOR COM ADIÇÃO DIRETA AO CRONOGRAMA ---
with aba2:
    st.subheader("Adicionar Serviços do SINAPI")
    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar (ex: Alvenaria, Concreto, Porcelanato)")
    
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        
        # A correção para o preço 0.0: agora forçamos a busca pela coluna preco_mediano corretamente mapeada
        query = f"""
            SELECT codigo, descricao, unidade, preco_mediano 
            FROM {tabela_alvo} 
            WHERE descricao ILIKE '%{busca}%' 
            LIMIT 20;
        """
        df_sinapi = conn.query(query, ttl=600)
        
        if not df_sinapi.empty:
            for index, row in df_sinapi.iterrows():
                
                with st.expander(f"📦 {row['descricao'][:60]}... | R$ {float(row['preco_mediano']):.2f} / {row['unidade']}"):
                    st.write(f"**Descrição Completa:** {row['descricao']}")
                    st.write(f"**Preço Unitário (RJ):** R$ {float(row['preco_mediano']):.2f} por {row['unidade']}")
                    
                    with st.form(f"add_direto_{index}"):
                        st.markdown("**Detalhes para o Cronograma:**")
                        col1, col2 = st.columns(2)
                        quantidade = col1.number_input(f"Quantidade ({row['unidade']})", min_value=0.1, value=1.0, step=1.0, format="%.2f")
                        fase_escolhida = col2.selectbox("Fase", ["Projetos", "Serviços Preliminares", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
                        
                        col3, col4 = st.columns(2)
                        data_inicio = col3.date_input("Início do Serviço")
                        data_fim = col4.date_input("Fim do Serviço")
                        
                        nome_abreviado = st.text_input("Nome Resumido para o Gráfico", value=row['descricao'][:50].title())
                        
                        btn_salvar = st.form_submit_button("➕ Adicionar Serviço à Obra")
                        
                        if btn_salvar:
                            if data_inicio > data_fim:
                                st.error("Data de início não pode ser maior que o término!")
                            else:
                                custo_total_servico = float(row['preco_mediano']) * quantidade
                                with conn.session as s:
                                    sql = text("""
                                        INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto) 
                                        VALUES (:nome, :fase, :inicio, :fim, 0, :custo)
                                    """)
                                    s.execute(sql, {"nome": nome_abreviado, "fase": fase_escolhida, "inicio": data_inicio, "fim": data_fim, "custo": custo_total_servico})
                                    s.commit()
                                st.success(f"Serviço adicionado! Custo total inserido: R$ {custo_total_servico:.2f}")
                                st.rerun()
        else:
            st.warning("Nenhum item encontrado.")

# --- ABA 3: ADICIONAR TAREFAS MANUAIS (SEM SINAPI) ---
with aba3:
    st.subheader("Adicionar Tarefa Manual (Personalizada)")
    st.info("Use esta aba para inserir tarefas administrativas, taxas de prefeitura ou serviços que não estão no SINAPI.")
    with st.form("form_nova_tarefa_manual"):
        nome = st.text_input("Nome da Tarefa")
        fase = st.selectbox("Fase", ["Projetos", "Administrativo", "Serviços Preliminares", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
        
        col1, col2 = st.columns(2)
        inicio = col1.date_input("Início")
        fim = col2.date_input("Término")
        
        custo = st.number_input("Custo Previsto Global (R$)", min_value=0.0, format="%.2f")
        conclusao = st.slider("Conclusão Atual (%)", 0, 100, 0)
        
        submit = st.form_submit_button("Salvar Tarefa Manual")
        
        if submit:
            if inicio > fim:
                st.error("A data de início não pode ser maior que o término!")
            else:
                with conn.session as s:
                    sql = text("""
                        INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto) 
                        VALUES (:nome, :fase, :inicio, :fim, :conc, :custo)
                    """)
                    s.execute(sql, {"nome": nome, "fase": fase, "inicio": inicio, "fim": fim, "conc": conclusao, "custo": custo})
                    s.commit()
                st.success("Tarefa manual salva!")
                st.rerun()

    st.divider()
    if st.button("Fazer Logout"):
        st.session_state["autenticado"] = False
        st.rerun()
