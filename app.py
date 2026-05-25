import os
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
from collections import defaultdict

st.set_page_config(
    page_title="CRM — Империя Купелей",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

def check_password():
    expected = st.secrets.get("PASSWORD") or os.environ.get("PASSWORD")
    if not expected:
        st.error("PASSWORD не задан. Добавь его в Settings → Secrets.")
        st.stop()
    if st.session_state.get("auth_ok"):
        return
    st.title("🔒 Вход")
    pwd = st.text_input("Пароль", type="password")
    if st.button("Войти"):
        if pwd == expected:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Неверный пароль")
    st.stop()

check_password()

WEBHOOK = st.secrets.get("WEBHOOK") or os.environ.get("WEBHOOK")
if not WEBHOOK:
    st.error("WEBHOOK не задан. Добавь его в Settings → Secrets.")
    st.stop()
COLORS = px.colors.qualitative.Set2

@st.cache_data(ttl=3600)
def b24(method, params=None):
    r = requests.post(f"{WEBHOOK}/{method}.json", json=params or {}, timeout=15)
    return r.json().get('result', [])

@st.cache_data(ttl=3600)
def get_all(method, params_str):
    params = eval(params_str)
    items, start = [], 0
    while True:
        p = dict(params); p['start'] = start
        result = b24(method, p)
        if isinstance(result, list) and result:
            items.extend(result)
            if len(result) < 50: break
            start += 50
        else: break
    return items

@st.cache_data(ttl=86400)
def load_reference():
    categories = b24('crm.dealcategory.list', {})
    all_stages = {}
    stage_order = {}
    for cid in ['0'] + ([c['ID'] for c in categories] if isinstance(categories, list) else []):
        stages = b24('crm.dealcategory.stage.list', {'id': cid})
        if isinstance(stages, list):
            cat_name = 'Общая' if cid == '0' else next((c['NAME'] for c in (categories or []) if c['ID'] == cid), cid)
            for s in stages:
                all_stages[s['STATUS_ID']] = {'name': s.get('NAME',''), 'category': cat_name, 'sort': int(s.get('SORT',0))}
                stage_order[s['STATUS_ID']] = int(s.get('SORT',0))
    sources_raw = b24('crm.status.list', {'filter': {'ENTITY_ID': 'SOURCE'}})
    source_names = {s['STATUS_ID']: s.get('NAME', s['STATUS_ID']) for s in (sources_raw if isinstance(sources_raw, list) else [])}
    lead_statuses_raw = b24('crm.status.list', {'filter': {'ENTITY_ID': 'STATUS'}})
    lead_statuses = {s['STATUS_ID']: s.get('NAME', s['STATUS_ID']) for s in (lead_statuses_raw if isinstance(lead_statuses_raw, list) else [])}
    return all_stages, source_names, lead_statuses

@st.cache_data(ttl=3600)
def load_users():
    users_raw = b24('user.get', {'filter': {'ACTIVE': True}})
    return {u['ID']: f"{u.get('NAME','')} {u.get('LAST_NAME','')}".strip() for u in (users_raw if isinstance(users_raw, list) else [])}

def load_data(date_from, date_to):
    date_from_str = date_from.strftime('%Y-%m-%dT00:00:00')
    date_to_str = date_to.strftime('%Y-%m-%dT23:59:59')
    leads = get_all('crm.lead.list', str({
        'filter': {'>=DATE_CREATE': date_from_str, '<=DATE_CREATE': date_to_str},
        'select': ['ID','STATUS_ID','ASSIGNED_BY_ID','SOURCE_ID','DATE_CREATE','OPPORTUNITY','TITLE']
    }))
    deals = get_all('crm.deal.list', str({
        'filter': {'>=DATE_CREATE': date_from_str, '<=DATE_CREATE': date_to_str},
        'select': ['ID','STAGE_ID','CATEGORY_ID','CLOSED','ASSIGNED_BY_ID','SOURCE_ID','DATE_CREATE','OPPORTUNITY','TITLE','CLOSEDATE']
    }))
    return leads, deals

# ─── HEADER ─────────────────────────────────────────────────────────────────
st.title("📊 CRM Аналитика — Империя Купелей")

col1, col2, col3, col4, col5 = st.columns([2, 2, 1, 2, 1])
with col1:
    date_from = st.date_input("С", value=datetime(2026, 4, 1).date())
with col2:
    date_to = st.date_input("По", value=datetime.now().date())
with col3:
    period = st.selectbox("Группировка", ["По дням", "По неделям", "По месяцам"])
with col4:
    rev_mode = st.selectbox("💰 Выручка считается с этапа", [
        "В производстве и далее",
        "Финальный счёт и далее",
        "Только выигранные"
    ])
with col5:
    st.write("")
    if st.button("🔄 Обновить", use_container_width=True):
        st.cache_data.clear()

# Стадии считающиеся выручкой
_rev_stages = {
    "В производстве и далее": {'EXECUTING','FINAL_INVOICE','WON',
                                'C2:EXECUTING','C2:FINAL_INVOICE','C2:WON',
                                'C4:EXECUTING','C4:FINAL_INVOICE','C4:WON',
                                'PREPAYMENT_INVOICE','C2:PREPAYMENT_INVOICE','C4:PREPAYMENT_INVOICE'},
    "Финальный счёт и далее": {'FINAL_INVOICE','WON',
                                'C2:FINAL_INVOICE','C2:WON',
                                'C4:FINAL_INVOICE','C4:WON'},
    "Только выигранные":      {'WON','C2:WON','C4:WON'},
}
REVENUE_STAGES = _rev_stages[rev_mode]

with st.spinner("Загружаю данные из Битрикс24..."):
    all_stages, source_names, lead_statuses = load_reference()
    users = load_users()
    leads, deals = load_data(date_from, date_to)

def gu(uid): return users.get(str(uid), f"ID:{uid}")

# Обработка лидов
leads_df = pd.DataFrame([{
    'Дата': l.get('DATE_CREATE','')[:10],
    'ID': l.get('ID'),
    'Название': (l.get('TITLE') or '')[:40],
    'Менеджер': gu(l.get('ASSIGNED_BY_ID')),
    'Источник': source_names.get(l.get('SOURCE_ID',''), l.get('SOURCE_ID') or 'Не указан'),
    'Статус': lead_statuses.get(l.get('STATUS_ID',''), l.get('STATUS_ID','')),
    'Конвертирован': l.get('STATUS_ID') == 'CONVERTED',
    'Мусор': l.get('STATUS_ID') == 'JUNK',
    'Сумма': float(l.get('OPPORTUNITY') or 0),
} for l in leads]) if leads else pd.DataFrame()

# Обработка сделок
deals_df = pd.DataFrame([{
    'Дата': d.get('DATE_CREATE','')[:10],
    'ID': d.get('ID'),
    'Название': (d.get('TITLE') or '')[:40],
    'Менеджер': gu(d.get('ASSIGNED_BY_ID')),
    'Источник': source_names.get(d.get('SOURCE_ID',''), d.get('SOURCE_ID') or 'Не указан'),
    'Воронка': all_stages.get(d.get('STAGE_ID',''), {}).get('category', '?'),
    'Стадия': all_stages.get(d.get('STAGE_ID',''), {}).get('name', d.get('STAGE_ID','')),
    'Сортировка': all_stages.get(d.get('STAGE_ID',''), {}).get('sort', 0),
    'Stage_ID': d.get('STAGE_ID',''),
    'Закрыта': d.get('CLOSED') == 'Y',
    'Успешна': 'WON' in d.get('STAGE_ID',''),
    'Провалена': 'LOSE' in d.get('STAGE_ID','') or 'APOLOGY' in d.get('STAGE_ID',''),
    'Сумма': float(d.get('OPPORTUNITY') or 0),
} for d in deals]) if deals else pd.DataFrame()

if not leads_df.empty:
    leads_df['Дата'] = pd.to_datetime(leads_df['Дата'])
if not deals_df.empty:
    deals_df['Дата'] = pd.to_datetime(deals_df['Дата'])
    deals_df['В_выручке'] = deals_df['Stage_ID'].isin(REVENUE_STAGES)

total_leads = len(leads_df)
converted = int(leads_df['Конвертирован'].sum()) if not leads_df.empty else 0
junk = int(leads_df['Мусор'].sum()) if not leads_df.empty else 0
active_leads = total_leads - converted - junk
conv_rate = round(converted / total_leads * 100, 1) if total_leads else 0
total_deals = len(deals_df)
won = int(deals_df['Успешна'].sum()) if not deals_df.empty else 0
lost = int(deals_df['Провалена'].sum()) if not deals_df.empty else 0
active_deals = int((~deals_df['Закрыта']).sum()) if not deals_df.empty else 0
in_revenue = int(deals_df['В_выручке'].sum()) if not deals_df.empty else 0
revenue = deals_df[deals_df['В_выручке']]['Сумма'].sum() if not deals_df.empty else 0
avg_deal = revenue / in_revenue if in_revenue else 0
pipeline = deals_df[~deals_df['Закрыта'] & ~deals_df['В_выручке']]['Сумма'].sum() if not deals_df.empty else 0

# ─── TABS ───────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Обзор", "🎯 Воронка", "📍 Источники", "👥 Менеджеры", "⚠️ Контроль дел"
])

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — ОБЗОР
# ═══════════════════════════════════════════════════════════════════════════
with tab1:
    # Ключевые показатели
    c1,c2,c3,c4,c5,c6 = st.columns(6)
    c1.metric("Лидов", total_leads)
    c2.metric("Конвертировано", f"{converted} ({conv_rate}%)")
    c3.metric("Сделок", total_deals, f"Активных: {active_deals}")
    c4.metric("Выиграно", won, f"Провалено: {lost}")
    c5.metric(f"Выручка ({rev_mode.split()[0]}…)", f"{revenue:,.0f} ₽", f"{in_revenue} сделок")
    c6.metric("Средний чек", f"{avg_deal:,.0f} ₽")

    st.divider()

    # Динамика лидов и сделок по времени
    if not leads_df.empty or not deals_df.empty:
        st.subheader("📅 Динамика")

        freq = {'По дням': 'D', 'По неделям': 'W', 'По месяцам': 'ME'}[period]

        dyn_data = []
        if not leads_df.empty:
            ld = leads_df.set_index('Дата').resample(freq).size().reset_index()
            ld.columns = ['Дата', 'Количество']
            ld['Тип'] = 'Лиды'
            dyn_data.append(ld)
        if not deals_df.empty:
            dd = deals_df.set_index('Дата').resample(freq).size().reset_index()
            dd.columns = ['Дата', 'Количество']
            dd['Тип'] = 'Сделки'
            dyn_data.append(dd)

        if dyn_data:
            dyn_df = pd.concat(dyn_data)
            fig = px.bar(dyn_df, x='Дата', y='Количество', color='Тип', barmode='group',
                         color_discrete_map={'Лиды': '#2E75B6', 'Сделки': '#4CAF50'})
            fig.update_layout(margin=dict(l=0,r=0,t=10,b=0), height=280, legend=dict(orientation='h', y=1.1))
            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("🎯 Воронка лидов")
        if not leads_df.empty:
            fig2 = go.Figure(go.Funnel(
                y=['Все лиды', 'Активные', 'Конвертировано'],
                x=[total_leads, active_leads, converted],
                textinfo="value+percent initial",
                marker_color=["#1F4E79", "#2E75B6", "#4CAF50"]
            ))
            fig2.update_layout(margin=dict(l=0,r=0,t=10,b=0), height=260)
            st.plotly_chart(fig2, use_container_width=True)

    with col_r:
        st.subheader("💰 Сделки: пайплайн")
        if not deals_df.empty:
            status_data = pd.DataFrame({
                'Статус': ['Пайплайн', 'В выручке', 'Провалено'],
                'Количество': [active_deals, in_revenue, lost],
                'Сумма': [
                    pipeline,
                    revenue,
                    deals_df[deals_df['Провалена']]['Сумма'].sum()
                ]
            })
            fig3 = px.bar(status_data, x='Статус', y='Сумма', text='Количество',
                          color='Статус',
                          color_discrete_map={'Пайплайн':'#2E75B6','В выручке':'#4CAF50','Провалено':'#E53935'})
            fig3.update_traces(texttemplate='%{text} сд.', textposition='outside')
            fig3.update_layout(margin=dict(l=0,r=0,t=10,b=0), height=260, showlegend=False,
                               yaxis_title="Сумма, ₽")
            st.plotly_chart(fig3, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — ВОРОНКА СДЕЛОК
# ═══════════════════════════════════════════════════════════════════════════
with tab2:
    if deals_df.empty:
        st.info("Нет данных за выбранный период")
    else:
        funnels = deals_df['Воронка'].unique().tolist()
        selected_funnel = st.selectbox("Воронка", ["Все"] + funnels)

        df_f = deals_df if selected_funnel == "Все" else deals_df[deals_df['Воронка'] == selected_funnel]

        st.subheader("📊 Сделки по стадиям")
        stages_count = (df_f.groupby(['Воронка','Стадия','Сортировка'])
                        .agg(Сделок=('ID','count'), Сумма=('Сумма','sum'))
                        .reset_index()
                        .sort_values(['Воронка','Сортировка']))

        col_a, col_b = st.columns(2)
        with col_a:
            fig = px.bar(stages_count, x='Стадия', y='Сделок', color='Воронка',
                         color_discrete_sequence=COLORS, text='Сделок')
            fig.update_traces(textposition='outside')
            fig.update_layout(margin=dict(l=0,r=0,t=10,b=40), height=360,
                              xaxis_tickangle=-35, showlegend=selected_funnel=="Все")
            st.plotly_chart(fig, use_container_width=True)

        with col_b:
            fig2 = px.bar(stages_count, x='Стадия', y='Сумма', color='Воронка',
                          color_discrete_sequence=COLORS, text_auto='.3s')
            fig2.update_layout(margin=dict(l=0,r=0,t=10,b=40), height=360,
                               xaxis_tickangle=-35, yaxis_title="Сумма, ₽",
                               showlegend=selected_funnel=="Все")
            st.plotly_chart(fig2, use_container_width=True)

        st.divider()
        st.subheader("📋 Детальная таблица по стадиям")
        stages_count['Сумма'] = stages_count['Сумма'].map('{:,.0f} ₽'.format)
        st.dataframe(stages_count[['Воронка','Стадия','Сделок','Сумма']],
                     use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — ИСТОЧНИКИ
# ═══════════════════════════════════════════════════════════════════════════
with tab3:
    if leads_df.empty:
        st.info("Нет данных за выбранный период")
    else:
        # ── Блок 1: Лиды по источникам ──────────────────────────────────
        st.subheader("📍 Лиды по источникам")
        src = (leads_df.groupby('Источник')
               .agg(Лидов=('ID','count'),
                    Конвертировано=('Конвертирован','sum'),
                    Мусор=('Мусор','sum'),
                    Сумма_лидов=('Сумма','sum'))
               .reset_index())
        src['Конверсия_в_сделку_%'] = (src['Конвертировано'] / src['Лидов'] * 100).round(1)
        src['Мусор_%'] = (src['Мусор'] / src['Лидов'] * 100).round(1)
        src = src.sort_values('Лидов', ascending=False)

        col_a, col_b = st.columns(2)
        with col_a:
            fig = px.bar(src, x='Лидов', y='Источник', orientation='h',
                         color='Конверсия_в_сделку_%', color_continuous_scale='Blues',
                         text='Лидов')
            fig.update_traces(textposition='outside')
            fig.update_layout(margin=dict(l=0,r=0,t=10,b=0), height=360,
                               title="Лидов по источнику")
            st.plotly_chart(fig, use_container_width=True)

        with col_b:
            fig2 = px.bar(src, x='Конверсия_в_сделку_%', y='Источник', orientation='h',
                          color='Конверсия_в_сделку_%', color_continuous_scale='Greens',
                          text='Конверсия_в_сделку_%')
            fig2.update_traces(texttemplate='%{text}%', textposition='outside')
            fig2.update_layout(margin=dict(l=0,r=0,t=10,b=0), height=360,
                               title="Конверсия лид → сделка, %")
            st.plotly_chart(fig2, use_container_width=True)

        st.divider()

        if not deals_df.empty:
            # ── Блок 2: Сквозная воронка по источникам ──────────────────
            st.subheader("🔽 Сквозная воронка по источникам")
            st.caption("Показывает сколько сделок из каждого источника дошло до каждой стадии")

            # Матрица: источник × стадия
            src_stage = (deals_df.groupby(['Источник','Стадия'])
                         .agg(Сделок=('ID','count'), Сумма=('Сумма','sum'))
                         .reset_index())

            # Выбор источника для детальной воронки
            all_sources = sorted(deals_df['Источник'].unique().tolist())
            sel_src = st.selectbox("Источник для детального просмотра", ["Все"] + all_sources)

            if sel_src != "Все":
                src_detail = src_stage[src_stage['Источник'] == sel_src].sort_values('Сделок', ascending=False)
            else:
                src_detail = (src_stage.groupby('Стадия')
                              .agg(Сделок=('Сделок','sum'), Сумма=('Сумма','sum'))
                              .reset_index().sort_values('Сделок', ascending=False))

            col_c, col_d = st.columns(2)
            with col_c:
                fig3 = px.bar(src_detail, x='Стадия', y='Сделок',
                              color='Сделок', color_continuous_scale='Blues',
                              text='Сделок')
                fig3.update_traces(textposition='outside')
                fig3.update_layout(margin=dict(l=0,r=0,t=10,b=60), height=380,
                                   xaxis_tickangle=-35, title="Количество сделок по стадиям")
                st.plotly_chart(fig3, use_container_width=True)

            with col_d:
                fig4 = px.bar(src_detail, x='Стадия', y='Сумма',
                              color='Сумма', color_continuous_scale='Teal',
                              text_auto='.3s')
                fig4.update_layout(margin=dict(l=0,r=0,t=10,b=60), height=380,
                                   xaxis_tickangle=-35, yaxis_title="₽",
                                   title="Сумма сделок по стадиям")
                st.plotly_chart(fig4, use_container_width=True)

            st.divider()

            # ── Блок 3: Тепловая карта источник × стадия ────────────────
            st.subheader("🗺️ Источник × Стадия (количество сделок)")
            pivot = (src_stage.pivot_table(index='Источник', columns='Стадия',
                                           values='Сделок', aggfunc='sum', fill_value=0))
            # Сортируем источники по общему количеству
            pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]

            fig5 = px.imshow(pivot, text_auto=True, aspect='auto',
                             color_continuous_scale='Blues',
                             labels=dict(color="Сделок"))
            fig5.update_layout(margin=dict(l=0,r=0,t=10,b=0), height=max(300, len(pivot)*35),
                               xaxis_tickangle=-40)
            st.plotly_chart(fig5, use_container_width=True)

            st.divider()

            # ── Блок 4: Сводная таблица источник → все этапы ────────────
            st.subheader("📊 Сводная таблица: конверсия по этапам")

            # Для каждого источника считаем ключевые метрики
            src_full = src[['Источник','Лидов','Конвертировано','Конверсия_в_сделку_%','Мусор','Мусор_%']].copy()

            deals_by_src = deals_df.groupby('Источник').agg(
                Сделок=('ID','count'),
                Выиграно=('Успешна','sum'),
                Провалено=('Провалена','sum'),
                Активных=('Закрыта', lambda x: (~x).sum()),
                Выручка=('Сумма', lambda x: deals_df.loc[x.index[deals_df.loc[x.index,'В_выручке']],'Сумма'].sum()
                         if deals_df.loc[x.index,'В_выручке'].any() else 0)
            ).reset_index()
            deals_by_src['Конверсия_в_успех_%'] = (deals_by_src['Выиграно'] / deals_by_src['Сделок'] * 100).round(1)

            full_tbl = src_full.merge(deals_by_src, on='Источник', how='left').fillna(0)
            full_tbl['Выручка'] = full_tbl['Выручка'].map('{:,.0f} ₽'.format)

            rename_map = {
                'Конверсия_в_сделку_%': 'Лид→Сделка %',
                'Мусор_%': 'Мусор %',
                'Конверсия_в_успех_%': 'Сделка→Успех %'
            }
            full_tbl = full_tbl.rename(columns=rename_map)
            cols_order = ['Источник','Лидов','Мусор','Мусор %','Конвертировано','Лид→Сделка %',
                          'Сделок','Активных','Выиграно','Сделка→Успех %','Провалено','Выручка']
            full_tbl = full_tbl[[c for c in cols_order if c in full_tbl.columns]]
            st.dataframe(full_tbl, use_container_width=True, hide_index=True)

            # ── Блок 5: Выручка по источникам ───────────────────────────
            st.divider()
            st.subheader("💰 Выручка по источникам")
            won_deals = deals_df[deals_df['В_выручке']]
            if not won_deals.empty:
                src_rev = (won_deals.groupby('Источник')
                           .agg(Сделок=('ID','count'), Выручка=('Сумма','sum'))
                           .reset_index().sort_values('Выручка', ascending=False))
                src_rev['Средний чек'] = (src_rev['Выручка'] / src_rev['Сделок']).round(0)
                fig6 = px.bar(src_rev, x='Источник', y='Выручка', text_auto='.3s',
                              color='Выручка', color_continuous_scale='Teal')
                fig6.update_layout(margin=dict(l=0,r=0,t=10,b=40), height=300,
                                   xaxis_tickangle=-30, yaxis_title="₽")
                st.plotly_chart(fig6, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 4 — МЕНЕДЖЕРЫ
# ═══════════════════════════════════════════════════════════════════════════
with tab4:
    if leads_df.empty:
        st.info("Нет данных за выбранный период")
    else:
        st.subheader("👥 Эффективность менеджеров")

        mgr_leads = leads_df.groupby('Менеджер').agg(
            Лидов=('ID','count'),
            Конвертировано=('Конвертирован','sum'),
            Мусор=('Мусор','sum')
        ).reset_index()
        mgr_leads['Конверсия %'] = (mgr_leads['Конвертировано'] / mgr_leads['Лидов'] * 100).round(1)

        if not deals_df.empty:
            mgr_deals = deals_df.groupby('Менеджер').agg(
                Сделок=('ID','count'),
                Успешных=('Успешна','sum'),
                Провалено=('Провалена','sum'),
                Выручка=('Сумма', lambda x: deals_df.loc[x.index[deals_df.loc[x.index,'В_выручке']],'Сумма'].sum() if deals_df.loc[x.index,'В_выручке'].any() else 0)
            ).reset_index()
            mgr_full = mgr_leads.merge(mgr_deals, on='Менеджер', how='left').fillna(0)
        else:
            mgr_full = mgr_leads.copy()
            mgr_full[['Сделок','Успешных','Провалено','Выручка']] = 0

        mgr_full = mgr_full.sort_values('Лидов', ascending=False)

        # Топ-карточки
        col_a, col_b = st.columns(2)
        with col_a:
            fig = px.bar(mgr_full, x='Менеджер', y=['Лидов','Конвертировано'],
                         barmode='group', color_discrete_map={'Лидов':'#2E75B6','Конвертировано':'#4CAF50'},
                         text_auto=True)
            fig.update_layout(margin=dict(l=0,r=0,t=30,b=40), height=340,
                              xaxis_tickangle=-20, title="Лиды и конверсия")
            st.plotly_chart(fig, use_container_width=True)

        with col_b:
            fig2 = px.bar(mgr_full.sort_values('Конверсия %', ascending=False),
                          x='Менеджер', y='Конверсия %',
                          color='Конверсия %', color_continuous_scale='Greens',
                          text='Конверсия %')
            fig2.update_traces(texttemplate='%{text}%', textposition='outside')
            fig2.update_layout(margin=dict(l=0,r=0,t=30,b=40), height=340,
                               xaxis_tickangle=-20, title="Конверсия лид → сделка, %")
            st.plotly_chart(fig2, use_container_width=True)

        st.divider()

        if not deals_df.empty:
            col_c, col_d = st.columns(2)
            with col_c:
                fig3 = px.bar(mgr_full, x='Менеджер', y=['Успешных','Провалено'],
                              barmode='stack',
                              color_discrete_map={'Успешных':'#4CAF50','Провалено':'#E53935'},
                              text_auto=True)
                fig3.update_layout(margin=dict(l=0,r=0,t=30,b=40), height=320,
                                   xaxis_tickangle=-20, title="Сделки: успех / провал")
                st.plotly_chart(fig3, use_container_width=True)

            with col_d:
                mgr_rev = mgr_full[mgr_full['Выручка'] > 0].sort_values('Выручка', ascending=False)
                if not mgr_rev.empty:
                    fig4 = px.bar(mgr_rev, x='Менеджер', y='Выручка',
                                  text_auto='.3s', color='Выручка',
                                  color_continuous_scale='Blues')
                    fig4.update_layout(margin=dict(l=0,r=0,t=30,b=40), height=320,
                                       xaxis_tickangle=-20, yaxis_title="₽",
                                       title="Выручка по менеджерам")
                    st.plotly_chart(fig4, use_container_width=True)

        st.divider()
        st.subheader("📋 Сводная таблица")
        tbl = mgr_full.copy()
        if 'Выручка' in tbl.columns:
            tbl['Выручка'] = tbl['Выручка'].map('{:,.0f} ₽'.format)
        st.dataframe(tbl.drop(columns=['Мусор'], errors='ignore'),
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📅 Динамика лидов по менеджерам")
        if not leads_df.empty:
            freq = {'По дням': 'D', 'По неделям': 'W', 'По месяцам': 'ME'}[period]
            mgr_dyn = (leads_df.set_index('Дата').groupby('Менеджер')
                       .resample(freq)['ID'].count().reset_index())
            mgr_dyn.columns = ['Менеджер','Дата','Лидов']
            fig5 = px.line(mgr_dyn, x='Дата', y='Лидов', color='Менеджер',
                           color_discrete_sequence=COLORS)
            fig5.update_layout(margin=dict(l=0,r=0,t=10,b=0), height=300,
                               legend=dict(orientation='h', y=1.1))
            st.plotly_chart(fig5, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 5 — КОНТРОЛЬ ДЕЛ
# ═══════════════════════════════════════════════════════════════════════════
with tab5:
    st.subheader("⚠️ Контроль качества дел в CRM")
    st.caption("Проверяет: есть ли у активных лидов и сделок открытые запланированные дела на ближайшие 5 дней")

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        run_check = st.button("🔍 Запустить проверку", use_container_width=True, type="primary")

    if run_check:
        with st.spinner("Проверяю дела по всем активным лидам и сделкам..."):
            today = datetime.now()
            threshold_dt = today + timedelta(days=5)

            active_l = get_all('crm.lead.list', str({
                'filter': {'!STATUS_ID': ['CONVERTED','JUNK']},
                'select': ['ID','TITLE','ASSIGNED_BY_ID','DATE_CREATE']
            }))
            active_d = get_all('crm.deal.list', str({
                'filter': {'CLOSED': 'N'},
                'select': ['ID','TITLE','ASSIGNED_BY_ID','DATE_CREATE']
            }))

            problems = []
            ok_count = 0

            def check_entity(eid, etype, etype_id, title, uid, created):
                acts = b24('crm.activity.list', {
                    'filter': {'OWNER_ID': eid, 'OWNER_TYPE_ID': etype_id, 'COMPLETED': 'N'},
                    'select': ['ID','SUBJECT','DEADLINE']
                })
                manager = gu(uid)
                if not acts or not isinstance(acts, list):
                    return {'Тип': etype, 'ID': eid, 'Название': (title or '')[:45],
                            'Менеджер': manager, 'Проблема': '❌ Нет дел', 'Дней': 9999}
                nearest = None
                for a in acts:
                    dl = a.get('DEADLINE','')
                    if not dl: continue
                    try:
                        dt = datetime.strptime(dl[:19], '%Y-%m-%dT%H:%M:%S')
                        if nearest is None or dt < nearest:
                            nearest = dt
                    except: pass
                if nearest is None:
                    return {'Тип': etype, 'ID': eid, 'Название': (title or '')[:45],
                            'Менеджер': manager, 'Проблема': '❌ Нет даты у дела', 'Дней': 9999}
                days = (nearest - today).days
                if nearest > threshold_dt:
                    return {'Тип': etype, 'ID': eid, 'Название': (title or '')[:45],
                            'Менеджер': manager,
                            'Проблема': f'⏰ Дело {nearest.strftime("%d.%m.%Y")} (+{days}д)',
                            'Дней': days}
                return None

            for item in active_l:
                r = check_entity(item['ID'], 'Лид', 1, item.get('TITLE'), item.get('ASSIGNED_BY_ID'), item.get('DATE_CREATE'))
                if r: problems.append(r)
                else: ok_count += 1

            for item in active_d:
                r = check_entity(item['ID'], 'Сделка', 2, item.get('TITLE'), item.get('ASSIGNED_BY_ID'), item.get('DATE_CREATE'))
                if r: problems.append(r)
                else: ok_count += 1

            total_checked = len(active_l) + len(active_d)
            problems.sort(key=lambda x: x['Дней'], reverse=True)
            prob_df = pd.DataFrame(problems)

        # Результаты
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Проверено", total_checked, f"Лидов: {len(active_l)}, Сделок: {len(active_d)}")
        r2.metric("✅ Всё ок", ok_count)
        r3.metric("❌ Нарушений", len(problems),
                  delta=f"{round(len(problems)/total_checked*100,1)}% от всех" if total_checked else None,
                  delta_color="inverse")
        r4.metric("Без дел", int(prob_df['Проблема'].str.startswith('❌').sum()) if not prob_df.empty else 0)

        if not prob_df.empty:
            st.divider()

            # Статистика по менеджерам
            st.subheader("По менеджерам")
            mgr_stat = (prob_df.groupby('Менеджер')
                        .agg(Нарушений=('ID','count'),
                             Без_дел=('Проблема', lambda x: (x.str.startswith('❌')).sum()),
                             Далёкая_дата=('Проблема', lambda x: (x.str.startswith('⏰')).sum()))
                        .reset_index().sort_values('Нарушений', ascending=False))

            col_g, col_h = st.columns(2)
            with col_g:
                fig = px.bar(mgr_stat, x='Менеджер', y=['Без_дел','Далёкая_дата'],
                             barmode='stack',
                             color_discrete_map={'Без_дел':'#E53935','Далёкая_дата':'#FF9800'},
                             text_auto=True,
                             labels={'Без_дел':'Нет дел','Далёкая_дата':'Дата далеко'})
                fig.update_layout(margin=dict(l=0,r=0,t=10,b=40), height=300,
                                  xaxis_tickangle=-20, legend=dict(orientation='h', y=1.1))
                st.plotly_chart(fig, use_container_width=True)

            with col_h:
                st.dataframe(mgr_stat, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("Детальный список нарушений")
            st.dataframe(prob_df.drop(columns=['Дней']),
                         use_container_width=True, hide_index=True)
        else:
            st.success("✅ Все активные лиды и сделки имеют актуальные дела!")
    else:
        st.info("Нажми «Запустить проверку» — скрипт проверит все активные лиды и сделки за текущую неделю")

        st.divider()
        st.subheader("📊 Итоги последней проверки (24.05.2026)")
        last_check = pd.DataFrame([
            {'Менеджер': 'Яна Адамбаева', 'Нарушений': 22, 'Нет дел': 2, 'Дата 31.12': 20},
            {'Менеджер': 'Мария Бережная', 'Нарушений': 15, 'Нет дел': 2, 'Дата 31.12': 13},
            {'Менеджер': 'Анна Рассказова', 'Нарушений': 11, 'Нет дел': 7, 'Дата 31.12': 4},
            {'Менеджер': 'Светлана Иванова', 'Нарушений': 2, 'Нет дел': 1, 'Дата 31.12': 1},
            {'Менеджер': 'Марина Кузьмина', 'Нарушений': 2, 'Нет дел': 2, 'Дата 31.12': 0},
            {'Менеджер': 'Наталья Фомина', 'Нарушений': 1, 'Нет дел': 1, 'Дата 31.12': 0},
        ])
        col_i, col_j = st.columns([1,1])
        with col_i:
            fig = px.bar(last_check, x='Менеджер', y=['Нет дел','Дата 31.12'],
                         barmode='stack', text_auto=True,
                         color_discrete_map={'Нет дел':'#E53935','Дата 31.12':'#FF9800'},
                         title="53 нарушения из 104 (51%)")
            fig.update_layout(margin=dict(l=0,r=0,t=40,b=40), height=320,
                              xaxis_tickangle=-20, legend=dict(orientation='h', y=1.1))
            st.plotly_chart(fig, use_container_width=True)
        with col_j:
            st.dataframe(last_check, use_container_width=True, hide_index=True)

st.caption(f"Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')} | Период: {date_from} — {date_to}")
