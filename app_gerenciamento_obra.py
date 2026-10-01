import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import text

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

# --- ABA 1: CRONOGRAMA E RESUMO FINANCEIRO ---
with aba1:
    df_tarefas = conn.query("SELECT * FROM tarefas ORDER BY data_inicio;", ttl=0)
    
    if not df_tarefas.empty:
        custo_total = df_tarefas['custo_previsto'].sum()
        st.metric(label="Custo Total Previsto da Obra", value=f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim'])
        
        fig = px.timeline(
            df_tarefas, x_start="data_inicio", x_end="data_fim", y="nome_servico", color="fase",
            hover_data=["conclusao_percentual", "custo_previsto"], title="Evolução da Obra"
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        # Formatando a tabela para exibição
        df_exibicao = df_tarefas[["nome_servico", "fase", "data_inicio", "data_fim", "custo_previsto", "conclusao_percentual"]].copy()
        df_exibicao['data_inicio'] = df_exibicao['data_inicio'].dt.strftime('%d/%m/%Y')
        df_exibicao['data_fim'] = df_exibicao['data_fim'].dt.strftime('%d/%m/%Y')
        st.dataframe(df_exibicao, hide_index=True)
    else:
        st.info("Nenhuma tarefa cadastrada.")

# --- ABA 2: BUSCADOR SINAPI (MATERIAIS E SERVIÇOS) ---
with aba2:
    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar (ex: Alvenaria, Concreto, Porcelanato)")
    
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        
        query = f"""
            SELECT codigo, descricao, unidade, preco_mediano 
            FROM {tabela_alvo} 
            WHERE descricao ILIKE '%{busca}%' 
            LIMIT 50;
        """
        df_sinapi = conn.query(query, ttl=600)
        
        if not df_sinapi.empty:
            st.dataframe(df_sinapi, hide_index=True, use_container_width=True)
        else:
            st.warning("Nenhum item encontrado. Tente sinônimos ou palavras mais curtas.")

# --- ABA 3: ADICIONAR TAREFAS (COM CUSTOS) ---
with aba3:
    st.subheader("Adicionar Nova Etapa")
    with st.form("form_nova_tarefa"):
        nome = st.text_input("Nome do Serviço")
        fase = st.selectbox("Fase", ["Projetos", "Serviços Preliminares", "Infraestrutura", "Superestrutura", "Instalações", "Acabamento"])
        
        col1, col2 = st.columns(2)
        inicio = col1.date_input("Início")
        fim = col2.date_input("Término")
        
        custo = st.number_input("Custo Previsto (R$)", min_value=0.0, format="%.2f")
        conclusao = st.slider("Conclusão Atual (%)", 0, 100, 0)
        
        submit = st.form_submit_button("Salvar Tarefa")
        
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
                st.success("Tarefa salva!")
                st.rerun()

    st.divider()
    if st.button("Fazer Logout"):
        st.session_state["autenticado"] = False
        st.rerun()
