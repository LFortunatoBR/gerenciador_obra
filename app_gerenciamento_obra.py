import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import text
import unicodedata
from fpdf import FPDF
import os
from datetime import datetime, timedelta, date
import pytz

def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')

# --- LISTA OFICIAL DE MACRO-ETAPAS (PADRÃO SINAPI/EAP) ---
FASES_DA_OBRA = [
    "1. Serviços Preliminares e Projetos",
    "2. Canteiro de Obras e Locação",
    "3. Movimento de Terra (Terraplenagem)",
    "4. Fundações e Contenções",
    "5. Superestrutura (Concreto/Aço/Madeira)",
    "6. Alvenaria e Paredes de Vedação",
    "7. Coberturas e Impermeabilizações",
    "8. Esquadrias, Portas e Janelas",
    "9. Instalações Hidrossanitárias e Gás",
    "10. Instalações Elétricas, Lógicas e SPDA",
    "11. Instalações de Combate a Incêndio",
    "12. Instalações Especiais e Climatização",
    "13. Revestimentos Internos e Externos",
    "14. Pisos e Rodapés",
    "15. Forros e Pinturas",
    "16. Louças, Metais e Acessórios",
    "17. Paisagismo e Urbanização",
    "18. Limpeza Final e Desmobilização",
    "19. Taxas, Licenças e Administrativo"
]

# --- PALETA DE CORES PARA O RELATÓRIO PDF ---
CORES_FASES_PDF = {
    "1. Serviços Preliminares e Projetos": "#cfd8dc",
    "2. Canteiro de Obras e Locação": "#b0bec5",
    "3. Movimento de Terra (Terraplenagem)": "#8d6e63",
    "4. Fundações e Contenções": "#795548",
    "5. Superestrutura (Concreto/Aço/Madeira)": "#ff9800",
    "6. Alvenaria e Paredes de Vedação": "#ffcc80",
    "7. Coberturas e Impermeabilizações": "#00bcd4",
    "8. Esquadrias, Portas e Janelas": "#4dd0e1",
    "9. Instalações Hidrossanitárias e Gás": "#4fc3f7",
    "10. Instalações Elétricas, Lógicas e SPDA": "#fff176",
    "11. Instalações de Combate a Incêndio": "#e57373",
    "12. Instalações Especiais e Climatização": "#ba68c8",
    "13. Revestimentos Internos e Externos": "#a5d6a7",
    "14. Pisos e Rodapés": "#81c784",
    "15. Forros e Pinturas": "#4caf50",
    "16. Louças, Metais e Acessórios": "#f48fb1",
    "17. Paisagismo e Urbanização": "#66bb6a",
    "18. Limpeza Final e Desmobilização": "#e0e0e0",
    "19. Taxas, Licenças e Administrativo": "#9e9e9e"
}

# --- FUNÇÕES DE ENGENHARIA (CALENDÁRIO DIAS ÚTEIS) ---
def add_bus_days(start_date, days):
    if days == 0: return start_date
    current = start_date
    added = 0
    step = 1 if days > 0 else -1
    while added < abs(days):
        current += timedelta(days=step)
        if current.weekday() < 5: 
            added += 1
    return current

def bus_days_between(start, end):
    days = 0
    curr = start
    while curr < end:
        if curr.weekday() < 5: days += 1
        curr += timedelta(days=1)
    return days

# --- MOTOR CPM (MS PROJECT) ---
def rodar_motor_cpm(conn, obra_id):
    with conn.session as s:
        result = s.execute(text("SELECT * FROM tarefas WHERE obra_id = :oid"), {"oid": obra_id}).mappings().all()
        t_dict = {t['id']: dict(t) for t in result}
        
        mudou = True
        loop = 0
        while mudou and loop < 50: 
            mudou = False
            loop += 1
            for t_id, t in t_dict.items():
                if t['dependencia_id']:
                    pred = t_dict.get(t['dependencia_id'])
                    if pred:
                        lag = t['lag_dias'] or 0
                        tipo = t['tipo_dep'] or 'TI'
                        
                        nova_ini = t['data_inicio']
                        if tipo == 'TI': nova_ini = add_bus_days(pred['data_fim'], lag + 1)
                        elif tipo == 'II': nova_ini = add_bus_days(pred['data_inicio'], lag)
                            
                        if nova_ini != t['data_inicio']:
                            duracao = bus_days_between(t['data_inicio'], t['data_fim'])
                            nova_fim = add_bus_days(nova_ini, max(0, duracao))
                            t['data_inicio'], t['data_fim'] = nova_ini, nova_fim
                            mudou = True
                            
        parents = set(t['parent_id'] for t in t_dict.values() if t['parent_id'])
        for p_id in parents:
            children = [t for t in t_dict.values() if t['parent_id'] == p_id]
            if children and p_id in t_dict:
                min_ini = min(c['data_inicio'] for c in children)
                max_fim = max(c['data_fim'] for c in children)
                sum_c = sum(c['custo_previsto'] or 0 for c in children)
                
                total_c = sum_c if sum_c > 0 else len(children)
                sum_perc = sum((c['conclusao_percentual']*(c['custo_previsto'] or 1))/total_c for c in children) if sum_c>0 else sum(c['conclusao_percentual'] for c in children)/len(children)
                
                if (t_dict[p_id]['data_inicio'] != min_ini or t_dict[p_id]['data_fim'] != max_fim or 
                    t_dict[p_id]['custo_previsto'] != sum_c or t_dict[p_id]['conclusao_percentual'] != int(sum_perc)):
                    t_dict[p_id]['data_inicio'], t_dict[p_id]['data_fim'] = min_ini, max_fim
                    t_dict[p_id]['custo_previsto'], t_dict[p_id]['conclusao_percentual'] = sum_c, int(sum_perc)
                    mudou = True

        for t_id, t in t_dict.items():
            s.execute(text("UPDATE tarefas SET data_inicio=:i, data_fim=:f, custo_previsto=:c, conclusao_percentual=:p WHERE id=:id"), 
                      {"i": t['data_inicio'], "f": t['data_fim'], "c": t['custo_previsto'], "p": t['conclusao_percentual'], "id": t_id})
        s.commit()

st.set_page_config(page_title="Gestor de Obras", page_icon="🏗️", layout="wide")

# ==========================================
# LOGIN & CONEXÃO
# ==========================================
def check_password():
    if "autenticado" not in st.session_state: st.session_state["autenticado"] = False
    if not st.session_state["autenticado"]:
        st.title("🔒 Acesso Restrito")
        if st.button("Entrar") if st.text_input("Senha", type="password") == st.secrets["senha_app"] else False:
            st.session_state["autenticado"] = True
            st.rerun()
        return False
    return True

if not check_password(): st.stop()

url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta)

# ==========================================
# BARRA LATERAL (PROJETOS)
# ==========================================
with st.sidebar:
    st.header("🏢 Seus Projetos")
    try: df_obras = conn.query("SELECT * FROM obras ORDER BY id;", ttl=0)
    except: st.error("Rode o SQL no Neon primeiro!"); st.stop()
        
    obra_ativa_id = None
    if not df_obras.empty:
        obras_dict = dict(zip(df_obras['id'], df_obras['nome']))
        obra_ativa_id = st.selectbox("Projeto Ativo:", options=list(obras_dict.keys()), format_func=lambda x: obras_dict[x])
        
        with st.expander("⚙️ Gerenciar Projeto", expanded=False):
            if st.button("🗑️ Excluir Projeto"):
                with conn.session as s:
                    s.execute(text("DELETE FROM obras WHERE id = :id"), {"id": int(obra_ativa_id)})
                    s.commit()
                st.rerun()
    else: st.warning("Crie um projeto.")
        
    with st.form("nova_obra"):
        nova = st.text_input("Nova Obra")
        if st.form_submit_button("Criar") and nova:
            with conn.session as s:
                s.execute(text("INSERT INTO obras (nome) VALUES (:n)"), {"n": nova}); s.commit()
            st.rerun()

if not obra_ativa_id: st.stop()

# --- CARREGA DADOS DO PROJETO ---
df_tarefas = conn.query("SELECT * FROM tarefas WHERE obra_id = :oid ORDER BY data_inicio, id;", params={"oid": int(obra_ativa_id)}, ttl=0)
opcoes_dep = {0: "Nenhuma"}
opcoes_parent = {0: "Nenhuma (É Macro-etapa raiz)"}
if not df_tarefas.empty:
    for _, r in df_tarefas.iterrows():
        nome = r['nome_servico'] if pd.notna(r['nome_servico']) else f"ID {r['id']}"
        opcoes_dep[r['id']] = nome
        if pd.isna(r['parent_id']): opcoes_parent[r['id']] = f"📦 {nome}"

aba1, aba4, aba2, aba3 = st.tabs(["📊 Gantt & EAP", "📈 Curva S (Baseline)", "💰 Orçamento (SINAPI)", "⚙️ Planejar Etapas"])

# ==========================================
# ABA 1: GANTT, EAP E RECALCULO
# ==========================================
with aba1:
    col_met, col_btn = st.columns([3, 1])
    with col_met:
        df_top_level = df_tarefas[df_tarefas['parent_id'].isna()]
        custo_total = df_top_level['custo_previsto'].sum() if not df_top_level.empty else 0
        st.metric("Custo Total Real (Projeto)", f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
    with col_btn:
        st.write("")
        if st.button("🔄 Recalcular Cronograma (CPM)", type="primary", use_container_width=True):
            rodar_motor_cpm(conn, int(obra_ativa_id))
            st.rerun()

    if not df_tarefas.empty:
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio']).dt.date
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim']).dt.date
        
        # --- GANTT ---
        df_tarefas['plot_ini'] = pd.to_datetime(df_tarefas['data_inicio'])
        df_tarefas['plot_fim'] = pd.to_datetime(df_tarefas['data_fim'])
        fig = px.timeline(df_tarefas, x_start="plot_ini", x_end="plot_fim", y="nome_servico", color="fase", title="Evolução (Ignora Fins de Semana)")
        fig.update_yaxes(autorange="reversed"); fig.update_layout(height=400, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
        # --- TABELA EAP (COM TOTAIS) ---
        st.subheader("📋 Estrutura Analítica do Projeto (EAP)")
        
        df_exib_rows = []
        # Ordena as fases extraídas para que apareçam na ordem correta da lista FASES_DA_OBRA
        fases_presentes = [f for f in FASES_DA_OBRA if f in df_tarefas['fase'].unique()] 
        
        for fase in fases_presentes:
            df_fase = df_tarefas[df_tarefas['fase'] == fase]
            
            for _, row in df_fase.iterrows():
                servico_nome = "  ↳ " + row['nome_servico'] if pd.notna(row['parent_id']) else "📦 " + row['nome_servico']
                df_exib_rows.append({
                    "id": row['id'],
                    "Serviço": servico_nome,
                    "Início": pd.to_datetime(row['data_inicio']).strftime('%d/%m/%Y'),
                    "Fim": pd.to_datetime(row['data_fim']).strftime('%d/%m/%Y'),
                    "conclusao_percentual": row['conclusao_percentual'],
                    "custo_previsto": row['custo_previsto']
                })
                
            total_fase = df_fase[df_fase['parent_id'].isna()]['custo_previsto'].sum()
            df_exib_rows.append({
                "id": None,
                "Serviço": f"➤ SUBTOTAL: {fase.upper()}",
                "Início": "", "Fim": "", "conclusao_percentual": None, "custo_previsto": total_fase
            })
            
        df_exib_rows.append({
            "id": None, "Serviço": "⭐ TOTAL GERAL DA OBRA", "Início": "", "Fim": "", "conclusao_percentual": None, "custo_previsto": custo_total
        })
        
        df_exib = pd.DataFrame(df_exib_rows)
        st.dataframe(df_exib, column_config={"id": None, "conclusao_percentual": st.column_config.NumberColumn("Conclusão %", format="%d"), "custo_previsto": st.column_config.NumberColumn("Custo R$", format="R$ %.2f")}, hide_index=True, use_container_width=True)

# ==========================================
# ABA 4: CURVA S E LINHA DE BASE
# ==========================================
with aba4:
    st.header("📈 Curva S e Linha de Base")
    col1, col2 = st.columns([3, 1])
    with col1:
        st.write("A **Linha de Base** tira uma fotografia do orçamento original para análise de Valor Agregado.")
    with col2:
        if st.button("📸 Salvar Linha de Base Atual"):
            with conn.session as s:
                s.execute(text("UPDATE tarefas SET base_inicio=data_inicio, base_fim=data_fim, base_custo=custo_previsto WHERE obra_id=:oid"), {"oid": int(obra_ativa_id)})
                s.commit()
            st.success("Fotografia Salva!")
            st.rerun()
            
    df_base = df_tarefas[df_tarefas['base_inicio'].notna()].copy()
    if not df_base.empty:
        min_d, max_d = df_base['base_inicio'].min(), df_base['base_fim'].max()
        datas_grafico = [min_d + timedelta(days=x) for x in range((max_d - min_d).days + 1)]
        
        pv_acumulado, acc = [], 0
        for d in datas_grafico:
            custo_do_dia = 0
            if d.weekday() < 5: 
                for _, r in df_base.iterrows():
                    if r['base_inicio'] <= d <= r['base_fim']:
                        dur = bus_days_between(r['base_inicio'], r['base_fim']) + 1
                        custo_do_dia += float(r['base_custo']) / dur
            acc += custo_do_dia
            pv_acumulado.append(acc)
            
        fig_s = go.Figure()
        fig_s.add_trace(go.Scatter(x=datas_grafico, y=pv_acumulado, mode='lines', name='Custo Planejado (Baseline)', line=dict(color='blue', width=4)))
        
        ev_total = sum(float(r['base_custo']) * (r['conclusao_percentual']/100) for _, r in df_base.iterrows() if pd.notna(r['base_custo']))
        hoje = datetime.now(pytz.timezone('America/Sao_Paulo')).date()
        fig_s.add_trace(go.Scatter(x=[hoje], y=[ev_total], mode='markers', name='Valor Agregado Realizado (EV)', marker=dict(color='green', size=15, symbol='star')))
        
        fig_s.update_layout(title="Curva S de Progresso Financeiro", xaxis_title="Tempo", yaxis_title="R$ Acumulado")
        st.plotly_chart(fig_s, use_container_width=True)
    else:
        st.warning("⚠️ Linha de base não definida. Salve a configuração atual primeiro no botão acima.")

# ==========================================
# ABA 2: BUSCADOR SINAPI
# ==========================================
with aba2:
    st.subheader("Adicionar do SINAPI à Obra Ativa")
    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar")
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        df_sinapi = conn.query(f"SELECT codigo, descricao, unidade, preco_mediano FROM {tabela_alvo} WHERE descricao ILIKE '%{busca}%' LIMIT 15;", ttl=600)
        
        if not df_sinapi.empty:
            for index, row in df_sinapi.iterrows():
                with st.expander(f"📦 {row['descricao'][:60]}... | R$ {float(row['preco_mediano']):.2f} / {row['unidade']}"):
                    with st.form(f"add_direto_{index}"):
                        col1, col2 = st.columns(2)
                        qtd = col1.number_input(f"Quantidade", min_value=0.1, value=1.0)
                        fase_esc = col2.selectbox("Fase", FASES_DA_OBRA)
                        c3, c4 = st.columns(2)
                        d_ini, d_fim = c3.date_input("Início"), c4.date_input("Término")
                        nome_abrev = st.text_input("Nome", value=row['descricao'][:50].title())
                        dep_escolhida = st.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
                        
                        if st.form_submit_button("➕ Adicionar"):
                            if d_ini > d_fim: st.error("Erro nas datas!")
                            else:
                                with conn.session as s:
                                    s.execute(text("""INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id) 
                                                      VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob)"""),
                                              {"n": nome_abrev, "f": fase_esc, "i": d_ini, "fim": d_fim, "c": float(row['preco_mediano'])*qtd, "d": None if dep_escolhida==0 else dep_escolhida, "ob": int(obra_ativa_id)})
                                    s.commit()
                                st.rerun()

# ==========================================
# ABA 3: PLANEJAR ETAPAS E KITS ÚNICA E CONSOLIDADA
# ==========================================
with aba3:
    st.subheader("⚙️ Planejamento Avançado")
    modo = st.radio("Método de Inserção:", ["Kits Rápido de Engenharia", "Tarefa Manual Detalhada (MS Project)"])
    st.divider()

    if modo == "Kits Rápido de Engenharia":
        st.write("Gera cadeias automáticas de serviço (CPM) descontando fins de semana.")
        kits = {
            "Fundações Rasas (Sapatas/Blocos)": [
                {"nome": "Escavação", "fase": "3. Movimento de Terra (Terraplenagem)"}, 
                {"nome": "Armação da Fundação", "fase": "4. Fundações e Contenções"}, 
                {"nome": "Concretagem", "fase": "4. Fundações e Contenções"}
            ],
            "Concretagem (Laje/Pilar)": [
                {"nome": "Fôrmas", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}, 
                {"nome": "Armação", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}, 
                {"nome": "Concretagem", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}
            ],
            "Alvenaria e Acabamento": [
                {"nome": "Alvenaria", "fase": "6. Alvenaria e Paredes de Vedação"}, 
                {"nome": "Chapisco", "fase": "13. Revestimentos Internos e Externos"}, 
                {"nome": "Reboco", "fase": "13. Revestimentos Internos e Externos"}
            ],
            "Forro de Gesso Acartonado (Drywall)": [
                {"nome": "Estruturação e Tabica", "fase": "15. Forros e Pinturas"}, 
                {"nome": "Emplacamento (Gesso)", "fase": "15. Forros e Pinturas"}, 
                {"nome": "Tratamento de Juntas", "fase": "15. Forros e Pinturas"}
            ],
            "Pintura de Paredes/Teto": [
                {"nome": "Selador/Fundo", "fase": "15. Forros e Pinturas"}, 
                {"nome": "Massa (Corrida/Acrílica)", "fase": "15. Forros e Pinturas"}, 
                {"nome": "Pintura (Acabamento)", "fase": "15. Forros e Pinturas"}
            ],
            "Porcelanato": [
                {"nome": "Contrapiso", "fase": "14. Pisos e Rodapés"}, 
                {"nome": "Assentamento", "fase": "14. Pisos e Rodapés"}, 
                {"nome": "Rejunte", "fase": "14. Pisos e Rodapés"}
            ],
            "Instalações Elétricas (Básicas)": [
                {"nome": "Tubulação/Eletrodutos", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}, 
                {"nome": "Enfiação/Cabeamento", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}, 
                {"nome": "Fechamento (Tomadas/Interruptores)", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}
            ],            
            "Instalações Hidráulicas": [
                {"nome": "Rasgos e Tubulação", "fase": "9. Instalações Hidrossanitárias e Gás"}, 
                {"nome": "Teste de Estanqueidade", "fase": "9. Instalações Hidrossanitárias e Gás"}, 
                {"nome": "Fechamento de Rasgos", "fase": "9. Instalações Hidrossanitárias e Gás"}
            ],
            "Telhado Colonial": [
                {"nome": "Madeiramento (Tesouras/Terças)", "fase": "7. Coberturas e Impermeabilizações"},
                {"nome": "Assentamento de Telhas", "fase": "7. Coberturas e Impermeabilizações"},
                {"nome": "Cumeeira e Rufos", "fase": "7. Coberturas e Impermeabilizações"}
            ]
        }
        kit_sel = st.selectbox("Sistema Construtivo:", list(kits.keys()))
        
        with st.form("form_kit"):
            dt_ini = st.date_input("Início da 1ª Etapa")
            cols = st.columns(len(kits[kit_sel]))
            dias_lst, custo_lst = [], []
            for i, etp in enumerate(kits[kit_sel]):
                with cols[i]:
                    st.markdown(f"**{etp['nome']}**")
                    dias_lst.append(st.number_input("Dias", 1, 100, 2, key=f"k_d_{i}"))
                    custo_lst.append(st.number_input("Custo R$", 0.0, format="%.2f", key=f"k_c_{i}"))
            
            pai_m = st.selectbox("Pertence a qual Macro-etapa?", options=list(opcoes_parent.keys()), format_func=lambda x: opcoes_parent[x])
            dep_m = st.selectbox("A 1ª etapa depende de quem?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            
            if st.form_submit_button("🚀 Gerar Cascata"):
                curr_date = dt_ini
                curr_dep = None if dep_m == 0 else dep_m
                val_pai = None if pai_m == 0 else pai_m
                
                with conn.session as s:
                    for i, etp in enumerate(kits[kit_sel]):
                        fim_calc = add_bus_days(curr_date, dias_lst[i] - 1)
                        res = s.execute(text("""
                            INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id, parent_id, tipo_dep, lag_dias) 
                            VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob, :pa, 'TI', 0) RETURNING id
                        """), {"n": etp['nome'], "f": etp['fase'], "i": curr_date, "fim": fim_calc, "c": custo_lst[i], "d": curr_dep, "ob": int(obra_ativa_id), "pa": val_pai})
                        
                        curr_dep = res.scalar()
                        curr_date = add_bus_days(fim_calc, 1)
                    s.commit()
                st.success("Kit gerado! Vá na Aba 1 e Recalcule o CPM.")
                
    else:
        st.write("Adicione tarefas isoladas com regras complexas de dependência e Lags.")
        with st.form("form_manual_unico"):
            n = st.text_input("Nome da Tarefa/Etapa")
            f = st.selectbox("Fase", FASES_DA_OBRA)
            
            c_pai, c_cst = st.columns(2)
            pai = c_pai.selectbox("Pertence à qual Macro-etapa?", options=list(opcoes_parent.keys()), format_func=lambda x: opcoes_parent[x])
            cst = c_cst.number_input("Custo Previsto (R$)", min_value=0.0)
            
            c_i, c_f = st.columns(2)
            i = c_i.date_input("Início Planejado")
            fm = c_f.date_input("Término Planejado")
            
            st.write("**Dependências Lógicas**")
            c1, c2, c3 = st.columns(3)
            dep = c1.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            tipo = c2.selectbox("Tipo de Ligação", ["TI (Término-Início)", "II (Início-Início)"])
            lag = c3.number_input("Lag/Espera (Dias)", value=0)
            
            if st.form_submit_button("Inserir no Cronograma"):
                if i > fm:
                    st.error("O Início não pode ser maior que o Término!")
                else:
                    with conn.session as s:
                        s.execute(text("""
                            INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, obra_id, parent_id, dependencia_id, tipo_dep, lag_dias) 
                            VALUES (:n, :f, :i, :fim, 0, :c, :ob, :pa, :d, :td, :lag)
                        """), {"n": n, "f": f, "i": i, "fim": fm, "c": cst, "ob": int(obra_ativa_id), "pa": None if pai==0 else pai, "d": None if dep==0 else dep, "td": tipo[:2], "lag": lag})
                        s.commit()
                    st.success("Tarefa Inserida! Vá a Aba 1 e clique em Recalcular Cronograma (CPM).")
