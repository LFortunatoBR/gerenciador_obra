import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy import text
import unicodedata
from fpdf import FPDF
import os
from datetime import datetime, timedelta
import pytz

def remover_acentos(texto):
    if pd.isna(texto): return ""
    texto = str(texto).replace("📦 ", "").replace("↳", "->").replace("➤", ">>").replace("⭐", "***")
    s = ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')
    return s.encode('latin1', 'ignore').decode('latin1')

# ==========================================
# PALETA DE CORES PARA AS MACRO-ETAPAS (GANTT E PDF)
# ==========================================
CORES_FASES = {
    "1. Serviços Preliminares e Projetos": "#2196F3",
    "2. Canteiro de Obras e Locação": "#FF9800",
    "3. Movimento de Terra (Terraplenagem)": "#795548",
    "4. Fundações e Contenções": "#9E9E9E",
    "5. Superestrutura (Concreto/Aço/Madeira)": "#607D8B",
    "6. Alvenaria e Paredes de Vedação": "#FF5722",
    "7. Coberturas e Impermeabilizações": "#00BCD4",
    "8. Esquadrias, Portas e Janelas": "#E91E63",
    "9. Instalações Hidrossanitárias e Gás": "#03A9F4",
    "10. Instalações Elétricas, Lógicas e SPDA": "#FFEB3B",
    "11. Instalações de Combate a Incêndio": "#F44336",
    "12. Instalações Especiais e Climatização": "#9C27B0",
    "13. Revestimentos Internos e Externos": "#8BC34A",
    "14. Pisos e Rodapés": "#4CAF50",
    "15. Forros e Pinturas": "#CDDC39",
    "16. Louças, Metais e Acessórios": "#009688",
    "17. Paisagismo e Urbanização": "#388E3C",
    "18. Limpeza Final e Desmobilização": "#BDBDBD",
    "19. Taxas, Licenças e Administrativo": "#673AB7"
}

# ==========================================
# MOTOR DE TABELAS PDF
# ==========================================
def gerar_tabela_pdf(pdf, df, col_widths, col_names, base_x=10, orient="P", max_y=275):
    def imprimir_cabecalho():
        pdf.set_fill_color(41, 128, 185)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", 'B', 9)
        pdf.set_xy(base_x, pdf.get_y())
        for name, w in zip(col_names, col_widths):
            pdf.cell(w, 8, remover_acentos(name), border=0, fill=True, align='C')
        pdf.ln(8)

    imprimir_cabecalho()
    
    pdf.set_text_color(40, 40, 40)
    pdf.set_font("Arial", '', 8)
    fill = False
    
    for idx, row in df.iterrows():
        row_data = [remover_acentos(str(x)) for x in row.values]
        
        max_lines = 1
        for text, w in zip(row_data, col_widths):
            width_text = pdf.get_string_width(text)
            if width_text > (w - 2):
                lines = int(width_text / (w - 2)) + 1
                if lines > max_lines: max_lines = lines
        
        line_height = 5
        row_height = max_lines * line_height
        
        if pdf.get_y() + row_height > max_y:
            pdf.add_page(orientation=orient)
            pdf.set_y(15)
            imprimir_cabecalho()
            pdf.set_text_color(40, 40, 40)
            pdf.set_font("Arial", '', 8)
            
        y_start = pdf.get_y()
        
        if fill:
            pdf.set_fill_color(240, 245, 250)
            pdf.rect(base_x, y_start, sum(col_widths), row_height, 'F')
        
        x_curr = base_x
        for text, w in zip(row_data, col_widths):
            pdf.set_xy(x_curr, y_start)
            pdf.multi_cell(w, line_height, text, border=0, align='C')
            x_curr += w
        
        pdf.set_draw_color(200, 200, 200)
        pdf.line(base_x, y_start + row_height, base_x + sum(col_widths), y_start + row_height)
        pdf.set_xy(base_x, y_start + row_height)
        fill = not fill

# --- LISTAS E FUNÇÕES DO CPM ---
FASES_DA_OBRA = list(CORES_FASES.keys())

def add_bus_days(start_date, days, dias_impedidos=[]):
    if days == 0: return start_date
    current = start_date; added = 0; step = 1 if days > 0 else -1
    while added < abs(days):
        current += timedelta(days=step)
        # Só soma o dia se não for fim de semana E não estiver na lista de Chuva Impeditiva do RDO
        if current.weekday() < 5 and current not in dias_impedidos: 
            added += 1
    return current

def bus_days_between(start, end, dias_impedidos=[]):
    days = 0; curr = start
    while curr < end:
        if curr.weekday() < 5 and curr not in dias_impedidos: 
            days += 1
        curr += timedelta(days=1)
    return days

def rodar_motor_cpm(conn, obra_id):
    # 1. Busca os dias de chuva impeditiva do RDO
    df_chuva = conn.query("SELECT data_relatorio FROM rdo WHERE obra_id = :oid AND clima = 'Chuva Impeditiva'", params={"oid": obra_id}, ttl=0)
    dias_chuva = pd.to_datetime(df_chuva['data_relatorio']).dt.date.tolist() if not df_chuva.empty else []

    with conn.session as s:
        result = s.execute(text("SELECT * FROM tarefas WHERE obra_id = :oid"), {"oid": obra_id}).mappings().all()
        t_dict = {t['id']: dict(t) for t in result}
        mudou = True; loop = 0
        while mudou and loop < 50: 
            mudou = False; loop += 1
            for t_id, t in t_dict.items():
                if t['dependencia_id']:
                    pred = t_dict.get(t['dependencia_id'])
                    if pred:
                        lag = t['lag_dias'] or 0; tipo = t['tipo_dep'] or 'TI'
                        nova_ini = t['data_inicio']
                        # O motor agora usa os 'dias_chuva' para empurrar o cronograma automaticamente!
                        if tipo == 'TI': nova_ini = add_bus_days(pred['data_fim'], lag + 1, dias_chuva)
                        elif tipo == 'II': nova_ini = add_bus_days(pred['data_inicio'], lag, dias_chuva)
                        
                        if nova_ini != t['data_inicio']:
                            duracao = bus_days_between(t['data_inicio'], t['data_fim'], dias_chuva)
                            t['data_inicio'] = nova_ini
                            t['data_fim'] = add_bus_days(nova_ini, max(0, duracao), dias_chuva)
                            mudou = True
                            
        # Atualiza as Macro-etapas
        parents = set(t['parent_id'] for t in t_dict.values() if t['parent_id'])
        for p_id in parents:
            children = [t for t in t_dict.values() if t['parent_id'] == p_id]
            if children and p_id in t_dict:
                min_ini = min(c['data_inicio'] for c in children)
                max_fim = max(c['data_fim'] for c in children)
                sum_c = sum(c['custo_previsto'] or 0 for c in children)
                total_c = sum_c if sum_c > 0 else len(children)
                sum_perc = sum((c['conclusao_percentual']*(c['custo_previsto'] or 1))/total_c for c in children) if sum_c>0 else sum(c['conclusao_percentual'] for c in children)/len(children)
                if (t_dict[p_id]['data_inicio'] != min_ini or t_dict[p_id]['data_fim'] != max_fim or t_dict[p_id]['custo_previsto'] != sum_c or t_dict[p_id]['conclusao_percentual'] != int(sum_perc)):
                    t_dict[p_id]['data_inicio'], t_dict[p_id]['data_fim'], t_dict[p_id]['custo_previsto'], t_dict[p_id]['conclusao_percentual'] = min_ini, max_fim, float(sum_c), int(sum_perc)
                    mudou = True
                    
        for t_id, t in t_dict.items():
            s.execute(text("UPDATE tarefas SET data_inicio=:i, data_fim=:f, custo_previsto=:c, conclusao_percentual=:p WHERE id=:id"), {"i": t['data_inicio'], "f": t['data_fim'], "c": float(t['custo_previsto'] or 0), "p": int(t['conclusao_percentual'] or 0), "id": t_id})
        s.commit()

# ==========================================
# SETUP DA PÁGINA E CONEXÃO
# ==========================================
st.set_page_config(page_title="ERP Obras", page_icon="🏗", layout="wide")

def check_password():
    if "autenticado" not in st.session_state: st.session_state["autenticado"] = False
    if not st.session_state["autenticado"]:
        st.title("🔒 Acesso Restrito ERP")
        if st.button("Entrar") if st.text_input("Senha", type="password") == st.secrets["senha_app"] else False:
            st.session_state["autenticado"] = True; st.rerun()
        return False
    return True

if not check_password(): st.stop()

url_correta = st.secrets["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://")
conn = st.connection("postgresql", type="sql", url=url_correta, pool_pre_ping=True, pool_recycle=300)
fuso_brasil = pytz.timezone('America/Sao_Paulo')
hoje = datetime.now(fuso_brasil).date()

# ==========================================
# BARRA LATERAL
# ==========================================
with st.sidebar:
    st.header("🏢 Diretoria & Projetos")
    try: df_obras = conn.query("SELECT * FROM obras ORDER BY id;", ttl=0)
    except: st.error("Rode o SQL no Neon!"); st.stop()
        
    obra_ativa_id, taxa_bdi = 0, 0.0
    obras_dict = {0: "🌐 VISÃO GLOBAL (DASHBOARD)"}
    if not df_obras.empty:
        obras_dict.update(dict(zip(df_obras['id'], df_obras['nome'])))
        
    obra_ativa_id = st.selectbox("Acessar:", options=list(obras_dict.keys()), format_func=lambda x: obras_dict[x])
    
    if obra_ativa_id != 0:
        taxa_bdi = float(df_obras[df_obras['id'] == obra_ativa_id]['bdi'].values[0])
        with st.expander("⚙️ Parametrizar Projeto", expanded=False):
            novo_bdi = st.number_input("Taxa de BDI (%)", min_value=0.0, max_value=100.0, value=taxa_bdi, step=0.1)
            novo_nome = st.text_input("Renomear Projeto:", value=obras_dict[obra_ativa_id])
            if st.button("💾 Salvar Parametros"):
                with conn.session as s:
                    s.execute(text("UPDATE obras SET nome = :n, bdi = :b WHERE id = :id"), {"n": novo_nome, "b": novo_bdi, "id": int(obra_ativa_id)})
                    s.commit()
                st.rerun()
    st.divider()
    with st.form("nova_obra"):
        if st.form_submit_button("Criar Nova Obra") and (nova := st.text_input("Novo Projeto")):
            with conn.session as s: s.execute(text("INSERT INTO obras (nome) VALUES (:n)"), {"n": nova}); s.commit()
            st.rerun()

if obra_ativa_id == 0:
    st.title("🌐 Dashboard Global da Diretoria")
    df_all_tarefas = conn.query("SELECT t.obra_id, t.custo_previsto, t.conclusao_percentual, t.parent_id, o.nome, o.bdi FROM tarefas t JOIN obras o ON t.obra_id = o.id WHERE t.parent_id IS NULL;", ttl=0)
    if not df_all_tarefas.empty:
        resumo = []
        for o_id in df_all_tarefas['obra_id'].unique():
            df_o = df_all_tarefas[df_all_tarefas['obra_id'] == o_id]
            c_tot = df_o['custo_previsto'].sum(); v_tot = c_tot * (1 + (float(df_o.iloc[0]['bdi'])/100))
            conc_med = sum(df_o['conclusao_percentual'] * df_o['custo_previsto']) / c_tot if c_tot > 0 else 0
            resumo.append({"Obra": df_o.iloc[0]['nome'], "Custo Total": c_tot, "Preço Venda": v_tot, "Avanço Físico (%)": conc_med})
        df_resumo = pd.DataFrame(resumo)
        c1, c2, c3 = st.columns(3)
        c1.metric("Carteira Total (VGV)", f"R$ {df_resumo['Preço Venda'].sum():,.2f}")
        c2.metric("Custo Físico Total", f"R$ {df_resumo['Custo Total'].sum():,.2f}")
        c3.metric("Lucro Bruto Projetado", f"R$ {(df_resumo['Preço Venda'].sum() - df_resumo['Custo Total'].sum()):,.2f}")
        fig_dash = px.bar(df_resumo, x="Obra", y="Avanço Físico (%)", text="Avanço Físico (%)", color="Avanço Físico (%)", color_continuous_scale="RdYlGn", range_y=[0, 100])
        st.plotly_chart(fig_dash, use_container_width=True)
        
    else:
        st.info("Nenhuma obra encontrada. Crie um novo projeto na barra lateral.")
        
    st.divider()
    
    # --- ÁREA DE GESTÃO E EXCLUSÃO DE PROJETOS ---
    st.subheader("🗑️ Gestão de Dados e Exclusão de Projetos")
    st.write("Atenção: A exclusão de um projeto é permanente e apagará todas as tarefas, finanças e medições associadas.")
    
    try:
        df_obras_gestao = conn.query("SELECT id, nome FROM obras ORDER BY id;", ttl=0)
    except Exception as e:
        df_obras_gestao = pd.DataFrame()
        
    if not df_obras_gestao.empty:
        with st.expander("Abrir Painel de Exclusão de Projetos", expanded=False):
            with st.form("form_excluir_projetos"):
                # Cria checkboxes para selecionar múltiplos projetos
                projetos_para_excluir = st.multiselect(
                    "Selecione os projetos que deseja DELETAR TOTALMENTE:",
                    options=df_obras_gestao['id'].tolist(),
                    format_func=lambda x: df_obras_gestao[df_obras_gestao['id'] == x]['nome'].values[0]
                )
                
                # Checkbox de confirmação de segurança
                confirmacao = st.checkbox("Tenho certeza de que desejo apagar permanentemente os dados selecionados.")
                
                # Botão de exclusão (formatado em vermelho para chamar atenção)
                if st.form_submit_button("Excluir Projetos Selecionados", type="primary"):
                    if not projetos_para_excluir:
                        st.warning("Selecione pelo menos um projeto para excluir.")
                    elif not confirmacao:
                        st.error("Por favor, marque a caixa de confirmação de segurança.")
                    else:
                        with conn.session as s:
                            for p_id in projetos_para_excluir:
                                # O ON DELETE CASCADE no banco (configurado no Passo 1 da migração)
                                # garante que tarefas, RDO, finanças e medições sejam apagadas automaticamente.
                                s.execute(text("DELETE FROM obras WHERE id = :id"), {"id": int(p_id)})
                            s.commit()
                        st.success(f"{len(projetos_para_excluir)} projeto(s) excluído(s) com sucesso!")
                        st.rerun()
    st.stop()

# ==========================================
# OBRA SELECIONADA
# ==========================================
st.title(f"🏗️ {obras_dict[obra_ativa_id]}")

df_tarefas = conn.query("SELECT * FROM tarefas WHERE obra_id = :oid ORDER BY data_inicio, id;", params={"oid": int(obra_ativa_id)}, ttl=0)
opcoes_dep, opcoes_parent = {0: "Nenhuma"}, {0: "Nenhuma (É Macro-etapa raiz)"}
if not df_tarefas.empty:
    for _, r in df_tarefas.iterrows():
        n_tarefa = r['nome_servico'] if pd.notna(r['nome_servico']) else f"ID {r['id']}"
        opcoes_dep[r['id']] = n_tarefa
        if pd.isna(r['parent_id']): opcoes_parent[r['id']] = f"📦 {n_tarefa}"

aba1, aba2, aba3, aba4, aba5, aba6, aba7 = st.tabs(["📊 Gantt & EAP", "📈 Curva S (Medição)", "💸 Financeiro", "🛒 Insumos (Curva ABC)", "📖 RDO", "💰 SINAPI", "⚙️ Planejar"])

# --- ABA 1: GANTT, ATUALIZAÇÃO, TABELA EAP E PDF ---
with aba1:
    col_met1, col_met2, col_btn = st.columns([2, 2, 1])
    df_top_level = df_tarefas[df_tarefas['parent_id'].isna()]
    custo_total = df_top_level['custo_previsto'].sum() if not df_top_level.empty else 0
    preco_venda = custo_total * (1 + (taxa_bdi/100))
    
    col_met1.metric("Custo Total (Interno)", f"R$ {custo_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    col_met2.metric(f"Preço de Venda (BDI {taxa_bdi}%)", f"R$ {preco_venda:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    if col_btn.button("🔄 Recalcular CPM", type="primary", use_container_width=True):
        rodar_motor_cpm(conn, int(obra_ativa_id)); st.rerun()
        
    with st.expander("📝 Atualizar Progresso ou Ajustar Prazos", expanded=False):
        t_edit = df_tarefas[df_tarefas['parent_id'].notna() | (df_tarefas['parent_id'].isna() & df_tarefas['dependencia_id'].notna())]
        if not t_edit.empty:
            t_id = st.selectbox("Selecione o Serviço:", t_edit['id'], format_func=lambda x: t_edit[t_edit['id']==x]['nome_servico'].values[0])
            t_row = t_edit[t_edit['id'] == t_id].iloc[0]
            c_perc, c_data = st.columns(2)
            n_perc = c_perc.slider("Conclusão (%)", 0, 100, int(t_row['conclusao_percentual']))
            n_data = c_data.date_input("Nova Data de Término", value=t_row['data_fim'])
            if st.button("💾 Salvar Atualização", type="secondary"):
                with conn.session as s:
                    s.execute(text("UPDATE tarefas SET conclusao_percentual = :p, data_fim = :df WHERE id = :id"), {"p": n_perc, "df": n_data, "id": int(t_id)})
                    s.commit()
                rodar_motor_cpm(conn, int(obra_ativa_id))
                st.rerun()

    if not df_tarefas.empty:
        df_tarefas['data_inicio'] = pd.to_datetime(df_tarefas['data_inicio']).dt.date
        df_tarefas['data_fim'] = pd.to_datetime(df_tarefas['data_fim']).dt.date
        
        # --- GRÁFICO INTERATIVO NO APP (Mais compacto verticalmente, barras finas e linhas horizontais) ---
        altura_app = max(400, len(df_tarefas) * 25) # Reduzido o multiplicador de 35 para 25 para compactar
        fig = px.timeline(df_tarefas, x_start="data_inicio", x_end="data_fim", y="nome_servico", color="fase", color_discrete_map=CORES_FASES, title="Evolução Lógica")
        
        fig.update_yaxes(autorange="reversed")
        
        # Reduz a espessura das barras
        fig.update_traces(width=0.5) 
        
        # Adiciona as linhas guias verticais E horizontais
        fig.update_layout(
            height=altura_app, 
            margin=dict(l=0, r=0, t=30, b=0),
            yaxis=dict(
                showgrid=True, 
                gridcolor='rgba(128, 128, 128, 0.2)' # Linha horizontal sutil entre os serviços
            ),
            xaxis=dict(
                dtick=604800000, # Semanal
                tickformat="%d/%m\n%Y", 
                showgrid=True, 
                gridcolor='rgba(128, 128, 128, 0.4)',
                gridwidth=1
            )
        )
        st.plotly_chart(fig, use_container_width=True)
        
        st.subheader("📋 Estrutura Analítica (EAP)")
        df_exib_rows = []
        
        fases_presentes = sorted(df_tarefas['fase'].unique(), key=lambda x: FASES_DA_OBRA.index(x) if x in FASES_DA_OBRA else 999)
        ordem_exibicao_grafico = [] 
        
        for fase in fases_presentes:
            df_fase = df_tarefas[df_tarefas['fase'] == fase]
            for _, row in df_fase.iterrows():
                nome_formatado = ("  ↳ " if pd.notna(row['parent_id']) else "📦 ") + row['nome_servico']
                df_exib_rows.append({
                    "id": row['id'], "Serviço": nome_formatado,
                    "Início": pd.to_datetime(row['data_inicio']).strftime('%d/%m/%Y'), "Fim": pd.to_datetime(row['data_fim']).strftime('%d/%m/%Y'),
                    "Conc. %": row['conclusao_percentual'], "Custo (R$)": row['custo_previsto'], "Venda (R$)": float(row['custo_previsto']) * (1 + (taxa_bdi/100))
                })
                ordem_exibicao_grafico.insert(0, {'nome': row['nome_servico'], 'inicio': pd.to_datetime(row['data_inicio']), 'fim': pd.to_datetime(row['data_fim']), 'fase': row['fase']})
                
            t_custo = df_fase[df_fase['parent_id'].isna()]['custo_previsto'].sum()
            df_exib_rows.append({"id": None, "Serviço": f"➤ SUBTOTAL: {fase.upper()}", "Início": "", "Fim": "", "Conc. %": None, "Custo (R$)": t_custo, "Venda (R$)": t_custo * (1 + (taxa_bdi/100))})
            
        df_exib_rows.append({"id": None, "Serviço": "⭐ TOTAL GERAL", "Início": "", "Fim": "", "Conc. %": None, "Custo (R$)": custo_total, "Venda (R$)": preco_venda})
        df_exib = pd.DataFrame(df_exib_rows)
        st.dataframe(df_exib, column_config={"id": None, "Custo (R$)": st.column_config.NumberColumn(format="R$ %.2f"), "Venda (R$)": st.column_config.NumberColumn(format="R$ %.2f")}, hide_index=True, use_container_width=True)

        st.divider()
        st.write("📄 **Exportar Cronogramas Oficiais (A3 Alta Resolução)**")
        c_pdf1, c_pdf2 = st.columns(2)
        tipo_pdf = "interno" if c_pdf1.button("🔒 Gerar PDF Interno (Custos)") else ("cliente" if c_pdf2.button("💼 Gerar PDF Cliente (Venda)") else None)
            
        if tipo_pdf:
            with st.spinner("Desenhando gráficos e estruturando as páginas em formato A3..."):
                import matplotlib.pyplot as plt
                import matplotlib.dates as mdates
                import matplotlib.patches as mpatches
                
                df_graf_ordenado = pd.DataFrame(ordem_exibicao_grafico)
                
                # Compacta a altura do gráfico gerado no PDF
                altura_grafico = max(5, len(df_tarefas) * 0.25) 
                fig_pdf, ax = plt.subplots(figsize=(20, altura_grafico), dpi=150)
                
                for idx, row in df_graf_ordenado.iterrows():
                    cor_barra = CORES_FASES.get(row['fase'], "#b0bec5")
                    # height=0.5 afina a barra no PDF
                    ax.barh(row['nome'], mdates.date2num(row['fim']) - mdates.date2num(row['inicio']), left=mdates.date2num(row['inicio']), height=0.5, color=cor_barra, edgecolor='black', linewidth=0.5)
                
                # Grids Verticais (Semanas) e Horizontais (Serviços)
                ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO)) 
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))
                ax.grid(axis='x', color='gray', linestyle='--', linewidth=0.5, alpha=0.7)
                ax.grid(axis='y', color='gray', linestyle=':', linewidth=0.5, alpha=0.4) # Grid Horizontal
                
                ax.margins(x=0.03)
                plt.xticks(rotation=45, ha='right', fontsize=9); plt.yticks(fontsize=9)
                
                fases_unicas = df_graf_ordenado['fase'].unique()
                patches = [mpatches.Patch(color=CORES_FASES.get(f, "#b0bec5"), label=f) for f in fases_unicas]
                ax.legend(handles=patches, bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=8, title="Fases (EAP)")
                
                caminho_img = "gantt_temp.png"
                plt.savefig(caminho_img, bbox_inches='tight'); plt.close(fig_pdf)

                pdf = FPDF(unit="mm", format="A3")
                
                # PÁGINA 1: GANTT (A3 PAISAGEM)
                pdf.add_page(orientation="L")
                pdf.set_font("Arial", "B", 18)
                pdf.set_fill_color(41, 128, 185); pdf.set_text_color(255, 255, 255)
                pdf.cell(400, 15, remover_acentos(f"Cronograma Oficial - {obras_dict[obra_ativa_id]}"), ln=True, align="C", fill=True)
                pdf.ln(5)
                
                pdf.set_font("Arial", "B", 14); pdf.set_text_color(40, 40, 40)
                titulo_valor = f"Custo Estimado: R$ {custo_total:,.2f}" if tipo_pdf == "interno" else f"Preco Total da Obra: R$ {preco_venda:,.2f}"
                pdf.cell(400, 10, remover_acentos(titulo_valor.replace(",", "X").replace(".", ",").replace("X", ".")), ln=True, align="C")
                pdf.image(caminho_img, x=5, w=410); pdf.ln(5)
                
                # PÁGINAS 2+: TABELA EAP (A3 RETRATO)
                pdf.add_page(orientation="P")
                pdf.set_font("Arial", "B", 16); pdf.set_text_color(40, 40, 40)
                pdf.cell(277, 10, "Estrutura Analitica do Projeto (EAP)", ln=True, align="C")
                pdf.ln(5)
                
                df_pdf_eap = df_exib.drop(columns=['id']).copy()
                if tipo_pdf == "interno":
                    df_pdf_eap = df_pdf_eap.drop(columns=['Venda (R$)'])
                    df_pdf_eap['Custo (R$)'] = df_pdf_eap['Custo (R$)'].apply(lambda x: f"R$ {float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                    col_names = ["Servico", "Inicio", "Termino", "Conc.", "Custo"]
                else:
                    df_pdf_eap = df_pdf_eap.drop(columns=['Custo (R$)'])
                    df_pdf_eap['Venda (R$)'] = df_pdf_eap['Venda (R$)'].apply(lambda x: f"R$ {float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                    col_names = ["Servico", "Inicio", "Termino", "Conc.", "Valor (Venda)"]
                    
                df_pdf_eap['Conc. %'] = df_pdf_eap['Conc. %'].apply(lambda x: f"{int(x)}%" if pd.notna(x) else "")
                
                col_widths = [145, 30, 30, 22, 50]
                gerar_tabela_pdf(pdf, df_pdf_eap, col_widths, col_names, base_x=10, orient="P", max_y=390)
                
                pdf.output("relatorio_gantt.pdf")
                with open("relatorio_gantt.pdf", "rb") as f: st.session_state['pdf_gantt'] = f.read()
                try: os.remove(caminho_img)
                except: pass
                
        if 'pdf_gantt' in st.session_state:
            st.success("Relatório A3 gerado com sucesso!")
            st.download_button("⬇️ Baixar PDF (A3)", data=st.session_state['pdf_gantt'], file_name="Cronograma_EAP.pdf", mime="application/pdf")

# --- ABA 2: CURVA S E MEDIÇÃO ---
with aba2:
    st.header("📈 Medição e Curva S")
    df_base = df_tarefas[df_tarefas['base_inicio'].notna()].copy()
    
    ev_venda = sum(float(r['custo_previsto']) * (1 + (taxa_bdi/100)) * (r['conclusao_percentual']/100) for _, r in df_tarefas.iterrows())
    datas_g, pv_acumulado = [], []
    
    if not df_base.empty:
        min_d, max_d = df_base['base_inicio'].min(), df_base['base_fim'].max()
        datas_g = [min_d + timedelta(days=x) for x in range((max_d - min_d).days + 1)]
        acc = 0
        for d in datas_g:
            c_dia = 0
            if d.weekday() < 5: 
                for _, r in df_base.iterrows():
                    if r['base_inicio'] <= d <= r['base_fim']:
                        c_dia += (float(r['base_custo']) * (1 + (taxa_bdi/100))) / (bus_days_between(r['base_inicio'], r['base_fim']) + 1)
            acc += c_dia; pv_acumulado.append(acc)
    
    if st.button("📄 Gerar PDF da Curva S e Medição"):
        with st.spinner("Gerando PDF da Curva S..."):
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            
            pdf = FPDF(orientation="L", unit="mm", format="A4")
            pdf.add_page()
            pdf.set_fill_color(41, 128, 185); pdf.set_text_color(255, 255, 255); pdf.set_font("Arial", "B", 16)
            pdf.cell(277, 12, remover_acentos(f"Relatorio de Medicao e Curva S - {obras_dict[obra_ativa_id]}"), ln=True, align="C", fill=True)
            pdf.ln(5)

            if not df_base.empty:
                fig_s_pdf, ax = plt.subplots(figsize=(10, 4), dpi=150)
                ax.plot(datas_g, pv_acumulado, color='#2980b9', linewidth=2, label='Planejado')
                ax.plot([hoje], [ev_venda], marker='*', color='#27ae60', markersize=15, label='Executado (Hoje)')
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m/%Y'))
                plt.xticks(rotation=45, ha='right', fontsize=8); plt.grid(True, linestyle='--', alpha=0.6); plt.legend()
                plt.tight_layout()
                plt.savefig('scurve_temp.png', bbox_inches='tight'); plt.close(fig_s_pdf)
                
                pdf.image('scurve_temp.png', x=15, w=260); pdf.ln(5)
                df_med = conn.query("SELECT * FROM medicoes WHERE obra_id = :oid", params={"oid": int(obra_ativa_id)}, ttl=0)
                faturado = df_med['valor_medido'].sum() if not df_med.empty else 0.0
                
                pdf.set_font("Arial", "B", 12); pdf.set_text_color(40,40,40)
                pdf.cell(90, 10, remover_acentos(f"Executado (Preco Venda): R$ {ev_venda:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")), border=1, align="C")
                pdf.cell(90, 10, remover_acentos(f"Ja Faturado: R$ {faturado:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")), border=1, align="C")
                pdf.cell(90, 10, remover_acentos(f"Saldo para Medicao: R$ {max(0, ev_venda - faturado):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")), border=1, align="C")
                
                pdf.output("relatorio_medicao.pdf")
                with open("relatorio_medicao.pdf", "rb") as f: st.download_button("⬇ Baixar PDF (Curva S)", data=f.read(), file_name="CurvaS_Medicao.pdf", mime="application/pdf")
                try: os.remove('scurve_temp.png')
                except: pass
            else:
                st.error("Salve a Baseline primeiro para gerar o PDF!")

    col_curva, col_med = st.columns([2, 1])
    with col_curva:
        if not df_base.empty:
            fig_s = go.Figure()
            fig_s.add_trace(go.Scatter(x=datas_g, y=pv_acumulado, mode='lines', name='Planejado (Venda)', line=dict(color='blue', width=4)))
            fig_s.add_trace(go.Scatter(x=[hoje], y=[ev_venda], mode='markers', name='Executado (EV)', marker=dict(color='green', size=15, symbol='star')))
            st.plotly_chart(fig_s, use_container_width=True)
        else:
            if st.button("📸 Salvar Baseline (Fotografia do Planejamento)"):
                with conn.session as s:
                    s.execute(text("UPDATE tarefas SET base_inicio=data_inicio, base_fim=data_fim, base_custo=custo_previsto WHERE obra_id=:oid"), {"oid": int(obra_ativa_id)})
                    s.commit(); st.rerun()

    with col_med:
        df_med = conn.query("SELECT * FROM medicoes WHERE obra_id = :oid ORDER BY data_medicao", params={"oid": int(obra_ativa_id)}, ttl=0)
        faturado = df_med['valor_medido'].sum() if not df_med.empty else 0.0
        saldo = ev_venda - faturado
        st.metric("Executado (Preço Venda)", f"R$ {ev_venda:,.2f}")
        st.metric("Já Faturado (Recebido)", f"R$ {faturado:,.2f}")
        st.metric("Saldo Liberado para Cobrança", f"R$ {max(0, saldo):,.2f}")
        
        if saldo > 0 and st.button("💰 Faturar Mês Atual"):
            with conn.session as s:
                s.execute(text("INSERT INTO medicoes (obra_id, data_medicao, valor_medido, percentual_obra) VALUES (:o, :d, :v, :p)"), 
                          {"o": int(obra_ativa_id), "d": hoje, "v": float(saldo), "p": (ev_venda/preco_venda)*100 if preco_venda>0 else 0})
                s.commit(); st.rerun()

# --- ABA 3: FLUXO DE CAIXA E DATAVIZ ---
with aba3:
    st.header("💸 Controle de Caixa e Dashboard Financeiro")
    
    df_fin = conn.query("SELECT id, tipo, descricao, valor, data_vencimento, status FROM financeiro WHERE obra_id = :oid ORDER BY data_vencimento", params={"oid": int(obra_ativa_id)}, ttl=0)
    
    # 📊 NOVO DASHBOARD FINANCEIRO (MÉTRICAS E GRÁFICOS)
    if not df_fin.empty:
        rec_pago = df_fin[(df_fin['tipo']=='Receita') & (df_fin['status']=='Pago')]['valor'].sum()
        des_pago = df_fin[(df_fin['tipo']=='Despesa') & (df_fin['status']=='Pago')]['valor'].sum()
        des_pendente = df_fin[(df_fin['tipo']=='Despesa') & (df_fin['status']=='Pendente')]['valor'].sum()
        
        c_m1, c_m2, c_m3 = st.columns(3)
        c_m1.metric("Saldo Real em Caixa", f"R$ {rec_pago - des_pago:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c_m2.metric("Despesas Pagas", f"R$ {des_pago:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c_m3.metric("Contas a Pagar (Pendentes)", f"R$ {des_pendente:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), delta="-Saída Futura", delta_color="inverse")
        
        st.write("📈 **Projeção de Fluxo Mensal (Receitas vs Despesas)**")
        df_graf_fin = df_fin.copy()
        # Converte a data para capturar apenas Mês e Ano para agrupamento
        df_graf_fin['mes_ano'] = pd.to_datetime(df_graf_fin['data_vencimento']).dt.strftime('%m/%Y')
        df_agrupado = df_graf_fin.groupby(['mes_ano', 'tipo'])['valor'].sum().reset_index()
        
        fig_fin = px.bar(df_agrupado, x='mes_ano', y='valor', color='tipo', barmode='group', 
                         color_discrete_map={'Receita': '#27ae60', 'Despesa': '#c0392b'},
                         labels={'mes_ano': 'Mês de Vencimento', 'valor': 'Montante (R$)'})
        fig_fin.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0), yaxis_tickformat="R$ ,.2f")
        st.plotly_chart(fig_fin, use_container_width=True)
        st.divider()

    c_f1, c_f2 = st.columns([1, 2])
    with c_f1:
        st.subheader("Lançar Novo Título")
        with st.form("form_fin"):
            f_tipo = st.radio("Tipo", ["Despesa", "Receita"])
            f_desc = st.text_input("Descrição (Ex: Cimento, Empreiteiro)")
            f_val = st.number_input("Valor (R$)", min_value=0.0)
            f_venc = st.date_input("Vencimento")
            f_stat = st.selectbox("Status", ["Pendente", "Pago"])
            if st.form_submit_button("Lançar no Caixa"):
                with conn.session as s:
                    s.execute(text("INSERT INTO financeiro (obra_id, tipo, descricao, valor, data_vencimento, status) VALUES (:o, :t, :d, :v, :dt, :s)"),
                              {"o": int(obra_ativa_id), "t": f_tipo, "d": f_desc, "v": f_val, "dt": f_venc, "s": f_stat})
                    s.commit(); st.rerun()
                    
        if not df_fin.empty:
            st.divider()
            st.subheader("🗑️ Excluir Lançamento")
            with st.form("form_delete_fin"):
                fin_id_del = st.selectbox(
                    "Selecione o registro para apagar:", 
                    df_fin['id'], 
                    format_func=lambda x: f"{df_fin[df_fin['id']==x]['tipo'].values[0][:3].upper()} - {df_fin[df_fin['id']==x]['descricao'].values[0]} (R$ {df_fin[df_fin['id']==x]['valor'].values[0]:.2f})"
                )
                if st.form_submit_button("Excluir Definitivamente"):
                    with conn.session as s:
                        s.execute(text("DELETE FROM financeiro WHERE id=:id"), {"id": int(fin_id_del)})
                        s.commit()
                    st.success("Lançamento excluído com sucesso!"); st.rerun()
                
    with c_f2:
        if not df_fin.empty:
            if st.button("📄 Gerar PDF do Livro Caixa"):
                # (O CÓDIGO DO PDF DO CAIXA FICA EXATAMENTE IGUAL AQUI)
                pdf = FPDF(orientation="P", unit="mm", format="A4")
                pdf.add_page()
                pdf.set_fill_color(41, 128, 185); pdf.set_text_color(255, 255, 255); pdf.set_font("Arial", "B", 16)
                pdf.cell(190, 12, remover_acentos(f"Controle de Caixa - {obras_dict[obra_ativa_id]}"), ln=True, align="C", fill=True)
                pdf.ln(5)
                pdf.set_font("Arial", "B", 12); pdf.set_text_color(40,40,40)
                pdf.cell(190, 8, remover_acentos(f"Saldo em Caixa: R$ {rec_pago - des_pago:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")), ln=True)
                pdf.ln(5)
                df_pdf_fin = df_fin.drop(columns=['id']).copy()
                df_pdf_fin['valor'] = df_pdf_fin['valor'].apply(lambda x: f"R$ {float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                df_pdf_fin['data_vencimento'] = pd.to_datetime(df_pdf_fin['data_vencimento']).dt.strftime('%d/%m/%Y')
                gerar_tabela_pdf(pdf, df_pdf_fin, [25, 75, 35, 25, 25], ["Tipo", "Descricao", "Valor", "Vencimento", "Status"], base_x=12.5, orient="P", max_y=275)
                pdf.output("relatorio_caixa.pdf")
                with open("relatorio_caixa.pdf", "rb") as f: st.download_button("⬇️ Baixar PDF (Caixa)", data=f.read(), file_name="Financeiro.pdf", mime="application/pdf")
            
            st.write("Edite qualquer campo diretamente na tabela abaixo e clique em Salvar.")
            df_edit_fin = st.data_editor(
                df_fin, 
                column_config={"id": None, "tipo": st.column_config.SelectboxColumn("Tipo", options=["Despesa", "Receita"]), "descricao": st.column_config.TextColumn("Descrição"), "valor": st.column_config.NumberColumn("Valor", format="R$ %.2f"), "data_vencimento": st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"), "status": st.column_config.SelectboxColumn("Status", options=["Pendente", "Pago"])}, 
                hide_index=True, use_container_width=True)
            if st.button("💾 Salvar Alterações da Tabela"):
                with conn.session as s:
                    for _, row in df_edit_fin.iterrows():
                        s.execute(text("UPDATE financeiro SET tipo=:t, descricao=:d, valor=:v, data_vencimento=:dt, status=:s WHERE id=:id"), {"t": row['tipo'], "d": row['descricao'], "v": row['valor'], "dt": row['data_vencimento'], "s": row['status'], "id": int(row['id'])})
                s.commit(); st.rerun()

# --- ABA 4: CURVA ABC ---
with aba4:
    st.header("🛒 Agenda de Compras e Contratos (Curva ABC)")
    
    if not df_tarefas.empty:
        df_abc = df_tarefas[df_tarefas['parent_id'].isna()].copy()
        if not df_abc.empty:
            df_abc = df_abc[['fase', 'nome_servico', 'data_inicio', 'custo_previsto']]
            df_abc.columns = ['Fase', 'Pacote', 'Data Limite', 'Custo (Verba)']
            df_abc = df_abc.sort_values(by='Custo (Verba)', ascending=False)
            t_abc = df_abc['Custo (Verba)'].sum()
            df_abc['% do Total'] = (df_abc['Custo (Verba)'] / t_abc) * 100
            df_abc['% Acumulado'] = df_abc['% do Total'].cumsum()
            
            if st.button("📄 Gerar PDF da Curva ABC"):
                import matplotlib.pyplot as plt
                fig_p, ax1 = plt.subplots(figsize=(12, 5), dpi=150)
                
                # Truncamos o texto em 35 caracteres, pois agora ele cresce dentro da barra
                nomes_pacotes = df_abc['Pacote'].str[:35] 
                
                # Desenhamos as barras azuis e a linha vermelha
                barras = ax1.bar(nomes_pacotes, df_abc['Custo (Verba)'], color='#4fc3f7')
                ax2 = ax1.twinx()
                ax2.plot(nomes_pacotes, df_abc['% Acumulado'], color='red', marker='o')
                
                # APAGA TODOS OS RÓTULOS EM BAIXO DO EIXO X PARA NÃO OUPAREM ESPAÇO
                ax1.set_xticks(range(len(nomes_pacotes)))
                ax1.set_xticklabels([]) 
                
                # ESCRITA DENTRO DA BARRA (A Sua Sugestão Visual)
                max_y = df_abc['Custo (Verba)'].max()
                for i, barra in enumerate(barras):
                    # Encontra o meio exato horizontal da barra
                    xval = barra.get_x() + barra.get_width() / 2
                    
                    # Define que o texto começará a 2% de distância do chão da barra
                    y_texto = max_y * 0.02
                    
                    # Se o texto ultrapassar a altura da barrinha, podemos pintá-lo ou redimensionar.
                    # Mas para o Pareto, alinhamento pela base (bottom) resolve.
                    texto = nomes_pacotes.iloc[i]
                    ax1.text(xval, y_texto, texto, rotation=90, ha='center', va='bottom', fontsize=7, color='black', weight='bold')
                
                # O tight_layout agora terá muito espaço de folga pois não há textos fora do gráfico
                plt.tight_layout()
                plt.savefig('pareto_temp.png', bbox_inches='tight'); plt.close(fig_p)
                
                pdf = FPDF(orientation="L", unit="mm", format="A4")
                pdf.add_page()
                pdf.set_fill_color(41, 128, 185); pdf.set_text_color(255, 255, 255); pdf.set_font("Arial", "B", 16)
                pdf.cell(277, 12, remover_acentos(f"Curva ABC de Insumos - {obras_dict[obra_ativa_id]}"), ln=True, align="C", fill=True)
                pdf.ln(5)
                pdf.image('pareto_temp.png', x=15, w=260); pdf.ln(5)
                
                df_pdf_abc = df_abc.drop(columns=['Fase']).copy()
                df_pdf_abc['Data Limite'] = pd.to_datetime(df_pdf_abc['Data Limite']).dt.strftime('%d/%m/%Y')
                df_pdf_abc['Custo (Verba)'] = df_pdf_abc['Custo (Verba)'].apply(lambda x: f"R$ {float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                df_pdf_abc['% do Total'] = df_pdf_abc['% do Total'].apply(lambda x: f"{x:.1f}%")
                df_pdf_abc['% Acumulado'] = df_pdf_abc['% Acumulado'].apply(lambda x: f"{x:.1f}%")
                
                gerar_tabela_pdf(pdf, df_pdf_abc, [120, 30, 40, 25, 25], ["Pacote de Contratacao", "Data Limite", "Custo Estimado", "% Total", "% Acumulado"], base_x=28.5, orient="L", max_y=185)
                
                pdf.output("relatorio_abc.pdf")
                with open("relatorio_abc.pdf", "rb") as f: st.download_button("⬇️ Baixar PDF (Curva ABC)", data=f.read(), file_name="CurvaABC.pdf", mime="application/pdf")
                try: os.remove('pareto_temp.png')
                except: pass

            st.dataframe(df_abc, column_config={"Data Limite": st.column_config.DateColumn(format="DD/MM/YYYY"), "Custo (Verba)": st.column_config.NumberColumn(format="R$ %.2f"), "% do Total": st.column_config.NumberColumn(format="%.1f%%"), "% Acumulado": st.column_config.NumberColumn(format="%.1f%%")}, hide_index=True, use_container_width=True)
            
            fig_abc = go.Figure()
            fig_abc.add_trace(go.Bar(x=df_abc['Pacote'], y=df_abc['Custo (Verba)'], name="Custo", marker_color='#4fc3f7'))
            fig_abc.add_trace(go.Scatter(x=df_abc['Pacote'], y=df_abc['% Acumulado'], mode='lines+markers', name="% Acumulado", yaxis='y2', line=dict(color='red', width=3)))
            fig_abc.update_layout(title="Gráfico de Pareto", yaxis=dict(title="Custo"), yaxis2=dict(title="% Acumulado", overlaying='y', side='right', range=[0, 110]), margin=dict(b=0))
            st.plotly_chart(fig_abc, use_container_width=True)

# --- ABA 5: RDO ---
with aba5:
    st.header("📖 Diário de Obra (RDO)")
    with st.expander("➕ Preencher RDO de Hoje", expanded=True):
        with st.form("form_rdo"):
            d_rdo = st.date_input("Data", value=hoje)
            clima = st.selectbox("Clima", ["Ensolarado", "Nublado", "Chuvoso", "Chuva Impeditiva"])
            efetivo = st.text_area("Efetivo na Obra", height=60)
            obs = st.text_area("Ocorrências", height=80)
            if st.form_submit_button("Salvar RDO"):
                with conn.session as s:
                    s.execute(text("INSERT INTO rdo (obra_id, data_relatorio, clima, efetivo, observacoes) VALUES (:o, :d, :c, :e, :obs)"), {"o": int(obra_ativa_id), "d": d_rdo, "c": clima, "e": efetivo, "obs": obs})
                    s.commit()
                st.rerun()
    df_rdo = conn.query("SELECT * FROM rdo WHERE obra_id = :oid ORDER BY data_relatorio DESC", params={"oid": int(obra_ativa_id)}, ttl=0)
    for _, row in df_rdo.iterrows():
        with st.expander(f"🗓️ {row['data_relatorio']} - {row['clima']}"):
            st.write(f"**Efetivo:** {row['efetivo']}")
            st.write(f"**Obs:** {row['observacoes']}")

# --- ABA 6: SINAPI ---
with aba6:
    st.subheader("Adicionar do SINAPI")
    
    # 🚀 NOVA FUNÇÃO COM CACHE (Guarda o resultado por 1 hora)
    @st.cache_data(ttl=3600)
    def buscar_sinapi_cache(busca_texto, tabela):
        # Usamos uma query pura aqui pois st.cache_data funciona melhor retornando DataFrames limpos
        return conn.query(f"SELECT codigo, descricao, unidade, preco_mediano FROM {tabela} WHERE descricao ILIKE '%{busca_texto}%' LIMIT 15;", ttl=0)

    tipo_busca = st.radio("O que deseja orçar?", ["Serviços Completos (Composições)", "Materiais Isolados (Insumos)"])
    busca = st.text_input("🔍 Buscar (ex: Alvenaria)")
    
    if busca:
        tabela_alvo = "sinapi_composicoes" if "Serviços" in tipo_busca else "sinapi_insumos"
        # O sistema agora chama a função com cache! Instantâneo nas buscas repetidas.
        df_sinapi = buscar_sinapi_cache(busca, tabela_alvo)
        
        for index, row in df_sinapi.iterrows():
            with st.expander(f"📦 {row['descricao'][:60]}... | R$ {float(row['preco_mediano']):.2f} / {row['unidade']}"):
                with st.form(f"add_sinapi_{index}"):
                    c1, c2 = st.columns(2)
                    qtd = c1.number_input("Quantidade", min_value=0.1, value=1.0)
                    fase_esc = c2.selectbox("Fase", FASES_DA_OBRA)
                    c3, c4, c5 = st.columns(3)
                    d_ini, d_fim = c3.date_input("Início"), c4.date_input("Término")
                    equipe = c5.number_input("Tamanho da Equipe", min_value=0, value=2, help="Quantos trabalhadores simultâneos?")
                    n_abrev = st.text_input("Nome", value=row['descricao'][:50].title())
                    dep_esc = st.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
                    if st.form_submit_button("➕ Adicionar"):
                        if d_ini > d_fim: st.error("Erro nas datas!")
                        else:
                            with conn.session as s:
                                s.execute(text("""INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id, codigo_sinapi, quantidade_sinapi, equipe_necessaria) 
                                                  VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob, :cod, :qs, :eq)"""),
                                          {"n": n_abrev, "f": fase_esc, "i": d_ini, "fim": d_fim, "c": float(row['preco_mediano'])*qtd, "d": None if dep_esc==0 else dep_esc, "ob": int(obra_ativa_id), "cod": row['codigo'], "qs": qtd, "eq": equipe})
                                s.commit()
                            st.rerun()

# --- ABA 7: PLANEJAR KITS ---
with aba7:
    st.subheader("⚙ Planejamento Avançado")
    modo = st.radio("Método:", ["Kits Rápidos", "Tarefa Manual"])
    if modo == "Kits Rápidos":
        kits = {
            "Fundações Rasas (Sapatas/Blocos)": [{"nome": "Escavação", "fase": "3. Movimento de Terra (Terraplenagem)"}, {"nome": "Armação da Fundação", "fase": "4. Fundações e Contenções"}, {"nome": "Concretagem", "fase": "4. Fundações e Contenções"}],
            "Concretagem (Laje/Pilar)": [{"nome": "Fôrmas", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}, {"nome": "Armação", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}, {"nome": "Concretagem", "fase": "5. Superestrutura (Concreto/Aço/Madeira)"}],
            "Alvenaria e Acabamento": [{"nome": "Alvenaria", "fase": "6. Alvenaria e Paredes de Vedação"}, {"nome": "Chapisco", "fase": "13. Revestimentos Internos e Externos"}, {"nome": "Reboco", "fase": "13. Revestimentos Internos e Externos"}],
            "Forro de Gesso (Drywall)": [{"nome": "Estruturação", "fase": "15. Forros e Pinturas"}, {"nome": "Emplacamento", "fase": "15. Forros e Pinturas"}, {"nome": "Juntas", "fase": "15. Forros e Pinturas"}],
            "Pintura": [{"nome": "Selador", "fase": "15. Forros e Pinturas"}, {"nome": "Massa", "fase": "15. Forros e Pinturas"}, {"nome": "Acabamento", "fase": "15. Forros e Pinturas"}],
            "Porcelanato": [{"nome": "Contrapiso", "fase": "14. Pisos e Rodapés"}, {"nome": "Assentamento", "fase": "14. Pisos e Rodapés"}, {"nome": "Rejunte", "fase": "14. Pisos e Rodapés"}],
            "Elétrica": [{"nome": "Tubulação", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}, {"nome": "Cabeamento", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}, {"nome": "Tomadas", "fase": "10. Instalações Elétricas, Lógicas e SPDA"}],
            "Hidráulica": [{"nome": "Tubulação", "fase": "9. Instalações Hidrossanitárias e Gás"}, {"nome": "Teste Estanqueidade", "fase": "9. Instalações Hidrossanitárias e Gás"}, {"nome": "Fechamento", "fase": "9. Instalações Hidrossanitárias e Gás"}],
            "Telhado Colonial": [{"nome": "Madeiramento (Tesouras/Terças)", "fase": "7. Coberturas e Impermeabilizações"}, {"nome": "Assentamento de Telhas", "fase": "7. Coberturas e Impermeabilizações"}, {"nome": "Cumeeira e Rufos", "fase": "7. Coberturas e Impermeabilizações"}]
        }
        kit_sel = st.selectbox("Sistema:", list(kits.keys()))
        with st.form("f_kit"):
            dt_ini = st.date_input("Início da 1ª Etapa")
            cols = st.columns(len(kits[kit_sel]))
            d_lst, c_lst = [], []
            for i, etp in enumerate(kits[kit_sel]):
                with cols[i]:
                    st.markdown(f"**{etp['nome']}**")
                    d_lst.append(st.number_input("Dias", 1, 100, 2, key=f"d_{i}"))
                    c_lst.append(st.number_input("Custo R$", 0.0, format="%.2f", key=f"c_{i}"))
            p_m = st.selectbox("Pertence a qual Macro?", options=list(opcoes_parent.keys()), format_func=lambda x: opcoes_parent[x])
            d_m = st.selectbox("A 1ª depende de quem?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            
            if st.form_submit_button("🚀 Gerar Cascata"):
                c_date = dt_ini; c_dep = None if d_m == 0 else d_m
                with conn.session as s:
                    for i, etp in enumerate(kits[kit_sel]):
                        f_calc = add_bus_days(c_date, d_lst[i] - 1)
                        res = s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, dependencia_id, obra_id, parent_id, tipo_dep) VALUES (:n, :f, :i, :fim, 0, :c, :d, :ob, :pa, 'TI') RETURNING id"),
                                        {"n": etp['nome'], "f": etp['fase'], "i": c_date, "fim": f_calc, "c": c_lst[i], "d": c_dep, "ob": int(obra_ativa_id), "pa": None if p_m==0 else p_m})
                        c_dep = res.scalar(); c_date = add_bus_days(f_calc, 1)
                    s.commit()
                st.rerun()
    else:
        with st.form("f_man"):
            n, f = st.text_input("Nome"), st.selectbox("Fase", FASES_DA_OBRA)
            c1, c2 = st.columns(2)
            p = c1.selectbox("Macro-etapa?", options=list(opcoes_parent.keys()), format_func=lambda x: opcoes_parent[x])
            cst = c2.number_input("Custo R$", min_value=0.0)
            i, fm = c1.date_input("Início"), c2.date_input("Término")
            c3, c4, c5 = st.columns(3)
            d = c3.selectbox("Depende de?", options=list(opcoes_dep.keys()), format_func=lambda x: opcoes_dep[x])
            tp = c4.selectbox("Ligação", ["TI", "II"]); l = c5.number_input("Lag", value=0)
            if st.form_submit_button("Inserir"):
                if i > fm: st.error("Erro nas datas!")
                else:
                    with conn.session as s:
                        s.execute(text("INSERT INTO tarefas (nome_servico, fase, data_inicio, data_fim, conclusao_percentual, custo_previsto, obra_id, parent_id, dependencia_id, tipo_dep, lag_dias) VALUES (:n, :f, :i, :fim, 0, :c, :ob, :pa, :d, :td, :lag)"),
                                  {"n": n, "f": f, "i": i, "fim": fm, "c": cst, "ob": int(obra_ativa_id), "pa": None if p==0 else p, "d": None if d==0 else d, "td": tp[:2], "lag": l})
                        s.commit()
                    st.rerun()
