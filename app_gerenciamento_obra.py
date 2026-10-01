import streamlit as st
import pandas as pd
import plotly.express as px

# Configuração da página
st.set_page_config(page_title="Gestor de Obras", page_icon="🏗️", layout="centered")

# ==========================================
# 1. TELA DE LOGIN (BARREIRA DE SEGURANÇA)
# ==========================================
def check_password():
    # Verifica se o usuário já está logado na sessão atual
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False

    if not st.session_state["autenticado"]:
        st.title("🔒 Acesso Restrito")
        st.write("Insira a senha para acessar o Gestor de Obras.")
        
        senha_digitada = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            if senha_digitada == st.secrets["senha_app"]:
                st.session_state["autenticado"] = True
                st.rerun() # Recarrega a página agora autenticado
            else:
                st.error("Senha incorreta!")
        return False
    return True

# Se a senha não estiver correta, o script para aqui
if not check_password():
    st.stop()

# ==========================================
# 2. CONEXÃO COM O BANCO NEON
# ==========================================
# Ajusta a URL para forçar o uso do pacote psycopg2 que instalamos
url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta)

# ==========================================
# 3. INTERFACE PRINCIPAL DO APLICATIVO
# ==========================================
st.title("🏗️ Planejador de Obras Integrado")
st.write("Bem-vindo! Você está conectado.")

aba1, aba2, aba3 = st.tabs(["📊 Cronograma", "💰 Orçamento (SINAPI)", "⚙️ Configurações"])

with aba1:
    st.subheader("Gráfico de Gantt")
    st.info("Aqui vamos puxar os dados da tabela 'tarefas' do seu banco Neon.")
    # Código do gráfico entrará aqui após criarmos as tabelas

with aba2:
    st.subheader("Consulta de Insumos (SINAPI)")
    busca = st.text_input("🔍 Buscar material ou serviço (ex: Cimento)")
    if busca:
        st.info("Aqui faremos a busca direto na tabela 'sinapi_insumos' do Neon.")

with aba3:
    st.subheader("Sair do Aplicativo")
    if st.button("Fazer Logout"):
        st.session_state["autenticado"] = False
        st.rerun()
