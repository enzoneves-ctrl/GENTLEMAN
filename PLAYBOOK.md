# Playbook Analítico — Retail / CPGs Brasil

Referência consolidada para análises de Cancel, Order Loss, GeoQueue, Frota, Incentivos e Catálogo no Brasil. Toda query e análise devem partir daqui.

---

## 0. Regras inegociáveis

1. **Tabela base de pedidos:** `RP_SILVER_DB_PROD.DES_PROD.ORDER_DIMENSIONS_BR`. Toda análise de pedido começa nela; demais entram via JOIN.
2. **Segmentação obrigatória:** sempre abrir **São Paulo** e **Rio de Janeiro** separados. Despriorizar visão Brasil consolidado.
3. **Apenas leitura** (SELECT). Account Snowflake: `RAPPIORG-HG51401`.
4. **Selecionar só colunas necessárias** — tabelas grandes (ORDER_DIMENSIONS 182 cols, PNL 378 cols, TIPIFICADOR 113 cols). Nunca `SELECT *`.

---

## 1. Filtros base obrigatórios

### 1.1 Para ORDER_DIMENSIONS_BR (CPGs)
```sql
WHERE country = 'BR'
  AND is_ops = TRUE
  AND COALESCE(is_repurchased_core, FALSE) = FALSE
  AND vertical_sub_group IN (
        'SUPER', 'EXPRESS', 'ESPECIALIZADAS',
        'LICORES', 'PHARMACY', 'ECOMMERCE', 'MASCOTAS'
  )
  AND vertical NOT IN ('TURBO', 'CARGO', 'RESTAURANTES', 'RESTAURANTS')
```

### 1.2 Filtro is_ops_andina (universo Silver completo)
```sql
WHERE (go.state IN ('finished','pending_review') OR go.state ILIKE '%cancel%')
  AND go.synthetic = FALSE
  AND oo.is_ops = TRUE
  AND oo.valid_cargo = 'non_cargo'
  AND go.vertical NOT IN ('SERVICES','RAPPI','RAPPI TRAVEL')
  AND odim.is_marketplace = FALSE
```

> `state IN ('finished','pending_review')` = pedido entregue. Usar só `'finished'` perde ~98% dos entregues.

### 1.3 Para ORDER_DIMENSIONS Gold (qualquer país)
```sql
WHERE COUNT_TO_GMV = TRUE
  AND COUNTRY = 'BR'
  AND COALESCE(CLOSED_AT, CREATED_AT)::DATE >= '<inicio>'
  AND COALESCE(CLOSED_AT, CREATED_AT)::DATE <  '<fim>'
-- não usar DATE_AGG (deprecated)
```

### 1.4 Para PNL (UE)
```sql
WHERE CLOSED_AT >= '<inicio>'
  AND CLOSED_AT <  '<fim>'
  AND COUNTRY = 'BR'
  AND COUNT_TO_GMV = TRUE
-- nunca usar MONTH para data (inclui reexpressões)
```

---

## 2. Segmentação

### 2.1 Cidade
```sql
CASE
    WHEN mzs.city_name = 'Grande São Paulo' THEN 'Grande São Paulo'
    WHEN mzs.city_name = 'Rio De Janeiro'   THEN 'Rio De Janeiro'
    ELSE 'other'
END AS city_group
```
Visões obrigatórias: SP isolado, RJ isolado, SP+RJ consolidado.

### 2.2 Vertical (group_andina)
```sql
CASE
    WHEN go.vertical_group = 'ECOMMERCE'
      OR go.vertical_sub_group IN ('SUPER','LIQUOR','PETS','PHARMACY','SPECIALIZED')
        THEN 'RETAIL'
    WHEN go.vertical_group = 'RESTAURANTS' AND go.vertical_sub_group = 'RESTAURANTS'
        THEN 'RESTAURANTS'
    WHEN go.vertical_group = 'RESTAURANTS' AND go.vertical_sub_group = 'TURBO_RESTAURANTS'
        THEN 'TURBO_REST'
    WHEN go.vertical_group IN ('RAPPICASH','RAPPIFAVOR')
        THEN 'AFC'
    WHEN go.vertical_sub_group = 'TURBO'
        THEN 'TURBO'
    ELSE 'OTHERS'
END AS group_andina
```

| group_andina | Composição | Cancel target |
|---|---|---|
| RETAIL | CPGs + ECOMMERCE | 4% |
| TURBO | TURBO | — |
| TURBO_REST | TURBO_RESTAURANTS | — |
| RESTAURANTS | RESTAURANTS | — |
| AFC | RAPPICASH, RAPPIFAVOR | — |
| OTHERS | demais | — |

> ⚠️ **Tabela ORDERS (Silver):** `go.vertical='CPGS'` inclui Turbo. Para Retail puro **obrigatório** `ORDER_DIMENSIONS_BR` (VERTICAL_GROUP + VERTICAL_SUB_GROUP).

### 2.3 Sub-verticais CPG (ordem de exibição)
1. SUPER · 2. PHARMACY · 3. PETS · 4. SPECIALIZED · 5. LIQUOR

### 2.4 Cidades CNS (earnings)
```python
CNS_CITIES_BR = [
    'BR|saopaulo','BR|riodejaneiro','BR|beloHorizonte',
    'BR|curitiba','BR|portoAlegre','BR|fortaleza','BR|recife'
]
# CITY LIKE 'BR|%' (COUNTRY pode ser NULL)
```

---

## 3. KPIs e targets

| KPI | Target | Escopo |
|---|---|---|
| Cancel Rate c/ Turbo | ≤ 3% | RETAIL + TURBO (meta principal) |
| Cancel Rate Retail puro | ≤ 4% | RETAIL ex-Turbo, ex-Cargo, ex-Rests |
| Cancel Rate ALL (SP&A oficial) | — | Todos verticais incl. Cargo |
| Order Loss (OL) | ≤ 1.2% | `BR_BALANCEO_GMVLOST` |
| %Delays+20 | — | MINUTES_LATE > 20 / finalizados |
| Acceptance Rate | ≥ 70% | SP |
| Orders in Queue SP | ≥ 80% | CLANS_GROUP = 'clã' em SP |
| Cancel Partner | ≤ 1.5% | Retail |

### 3.1 Três definições de Cancel Rate

| Definição | Escopo | Tabela | Onde aparece |
|---|---|---|---|
| **ALL** | todos verticais ops (sem filtro de vertical), `VALID_CARGO != 'non_valid_cargo'`, SEM exclusão de fraude/teste | ORDER_DIMENSIONS_BR + CANCELLATIONS_BR | Report oficial SP&A (PowerBI) |
| **Retail puro (4%)** | `VERTICAL_GROUP IN ('CPGS','ECOMMERCE') AND VERTICAL_SUB_GROUP NOT ILIKE '%TURBO%'`, exclui `LEVEL_3 IN ('charge_back_fraud','inner_test')` | ORDER_DIMENSIONS_BR | Daily Slack, WBR BR |
| **Retail + Turbo (3%)** | RETAIL + TURBO, sem Rests/Cargo | ORDER_DIMENSIONS_BR | WBR BR |

Cancel base:
```sql
COUNT(CASE WHEN C.EFFECTIVE_REPURCHASE = FALSE AND O.IS_CANCELLED = TRUE
           THEN O.ORDER_ID END)::FLOAT
/ NULLIF(COUNT(O.ORDER_ID), 0)
```

### 3.2 Order Loss
```sql
-- BR_BALANCEO_GMVLOST é agregada; NÃO fazer COUNT
ROUND(DIV0(SUM(g.numerador_ord), SUM(g.denominador_ord)) * 100, 2) AS ol_pct
```

### 3.3 %Delays+20
```sql
DIV0(
  COUNT(DISTINCT IFF(bo.minutes_late >= 20, order_id, NULL)),
  COUNT(DISTINCT IFF(is_ops_andina, order_id, NULL))
) * 100
```

### 3.4 Earning semanal courier
```sql
-- agrupar por BUNDLE_ID (não ORDER_ID — duplica)
SELECT city, day_of_week, SUM(bundle_earning) AS total_earning
FROM rp_silver_db_prod.ful_core_ds.br_cns_earnings_calculated_information
WHERE city LIKE 'BR|%' AND order_state = 'finish'
GROUP BY 1, 2
```

### 3.5 Comparações temporais
| Relatório | Comparação |
|---|---|
| Daily Report | DoD (D-1 vs D-2 e D-8) |
| WBR BR | WoW (W-1 vs W-2) |
| Fulfillment Report | WoW |
| WBR COO | WTD vs mesmo período semana anterior |

---

## 4. Mapa de tabelas

### 4.1 Pedidos
| Tabela | Banco.schema | Uso |
|---|---|---|
| ORDER_DIMENSIONS_BR ⭐ | RP_SILVER_DB_PROD.DES_PROD | Base de pedidos (CPGs) |
| ORDER_DIMENSIONS (Gold) | RP_GOLD_DB_PROD.DES_PROD | ~1.97B linhas; Gold |
| ORDERS_BR | RP_SILVER_DB_PROD.DES_PROD | Silver base |
| OPS_ORDERS_BR | RP_SILVER_DB_PROD.DES_PROD | Flags ops (is_ops, valid_cargo) |
| ORDER_TIMES_BR | RP_SILVER_DB_PROD.DES_PROD | Tempos por etapa + E2E |
| ORDERS_GMV_BR | RP_SILVER_DB_PROD.DES_PROD | GMV por pedido |
| CANCELLATIONS_BR | RP_SILVER_DB_PROD.DES_PROD | Tipificação 3 níveis + EFFECTIVE_REPURCHASE |
| BAD_ORDERS | RP_SILVER_DB_PROD.DES_PROD | Cancel + defect + atraso |
| ORDER_MODIFICATIONS | FIVETRAN.BR_CORE_ORDERS_PUBLIC | Timeline de eventos |
| BR_ORDERS_PBI | RP_SILVER_DB_PROD.OPS_BR | **Fonte do Daily Report Slack** |
| PNL | RP_GOLD_DB_PROD.UE | P&L nível ordem (378 cols) |

### 4.2 Produto / Item
| Tabela | Banco.schema | Uso |
|---|---|---|
| ORDER_PRODUCTS_BR | RP_SILVER_DB_PROD.DES_PROD | 1 linha = 1 item |
| TBL_TIPIFICADOR_BR ⭐ | RP_SILVER_DB_PROD.CPGS_LOCAL_ANALYTICS | Stockout/substituição + catálogo (113 cols) |
| BR_NEW_CATALOG_INTEGRATION_METRICS_METADATA | FIVETRAN.CPG_INTEGRATIONS | Snapshot integração |
| PRODUCT_AVAILABILITY ⭐ | FIVETRAN.BR_amysql_...inventory_manager | **Fonte da verdade para Security Stock** |

### 4.3 Loja / Geografia
| Tabela | Banco.schema | Uso |
|---|---|---|
| TBL_DIM_STORES ⭐ | RP_SILVER_DB_PROD.CPGS_LOCAL_ANALYTICS | Dimensão + ponte de IDs + AGM (=KAM) |
| STORES_BR | RP_SILVER_DB_PROD.DES_PROD | Silver |
| MICROZONES_BR | RP_SILVER_DB_PROD.DES_PROD | Zonas |
| CPGS_NEW_PARADIMG_STORES | FIVETRAN.OPS_PL_PDT | Lojas Novo Paradigma |

### 4.4 GeoQueue
| Tabela | Banco.schema | Uso |
|---|---|---|
| GEO_QUEUE_STORE | FIVETRAN.BR_PG_MS_CNS_STORES_MS_PUBLIC | loja → geoqueue |
| GEO_QUEUE_POINT | FIVETRAN.BR_PG_MS_CNS_STORES_MS_PUBLIC | Nome/dados do ponto |
| GEO_QUEUE_POINT_CONFIGURATION | FIVETRAN.BR_PG_MS_CNS_STORES_MS_PUBLIC | Vigência |
| GEOQUEUES_SC_TURBORESTAURANTS_TBL | RP_SILVER_DB_PROD.OPS_BAL | Adoção por pedido |
| STOREKEEPER_PERFORMANCE | FIVETRAN.BR_PG_MS_CNS_PERFORMANCE_MS_PUBLIC | Takens, liberations, notifications |

### 4.5 Incentivos / Frota
| Tabela | Banco.schema | Uso |
|---|---|---|
| ULTIMATE_INCENTIVES | RP_SILVER_DB_PROD.FUL_CORE_DS | Catálogo de incentivos (≥ jul/2025) |
| BR_CNS_EARNINGS_CALCULATED_INFORMATION | RP_SILVER_DB_PROD.FUL_CORE_DS | Earnings por bundle |
| BR_COURIER_PRODUCTIVITY | RP_SILVER_DB_PROD.FUL_FORECASTING | Disponibilidade courier × dia × turno |
| BR_BALANCEO_GMVLOST | RP_GOLD_DB_PROD.FUL_CORE_DS | Order Loss |

---

## 5. IDs de loja — ⚠️ crítico

| ID | O que é | Onde |
|---|---|---|
| `STORE_ID` | Loja virtual | ORDER_DIMENSIONS_BR, TIPIFICADOR |
| `PHYSICAL_STORE_ID` | Loja física operacional | TBL_DIM_STORES (capacidade/pivot) |
| `CP_PHYSICAL_ID` / `CP_STORE_ID` | Content Portal (catálogo) | TIPIFICADOR / TBL_DIM_STORES |

**Regra:** usar `TBL_DIM_STORES` como ponte sempre que precisar trocar entre tipos de ID.

---

## 6. JOIN padrão (Silver)
```sql
FROM rp_silver_db_prod.des_prod.orders_br AS go
LEFT JOIN rp_silver_db_prod.des_prod.ops_orders_br   oo
       ON go.country = oo.country AND go.order_id = oo.order_id
LEFT JOIN rp_silver_db_prod.des_prod.stores_br        s
       ON go.country = s.country  AND go.store_id = s.store_id
LEFT JOIN rp_silver_db_prod.des_prod.orders_gmv_br    gp
       ON go.country = gp.country AND go.order_id = gp.order_id
LEFT JOIN rp_silver_db_prod.des_prod.microzones_br    mzs
       ON go.country = mzs.country AND go.store_microzone_id = mzs.microzone_id
LEFT JOIN rp_gold_db_prod.des_prod.order_dimensions   odim
       ON go.country = odim.country AND go.order_id = odim.order_id
LEFT JOIN rp_silver_db_prod.des_prod.cancellations_br c
       ON go.country = c.country  AND go.order_id = c.order_id
```

### 6.1 CTE GeoQueue vigente
```sql
BR_STORE_GEOQUEUE AS (
  SELECT 'BR' AS country, gqs.store_id, gqs.point_id AS store_geo_queue,
         gqp.name AS geo_name, c.created_at AS geo_created_at,
         CASE WHEN gqp.name = 'Without Geo' THEN 'Without Geo'
              WHEN gqp.name IS NOT NULL    THEN 'clã'
              ELSE NULL END AS clans_group
  FROM fivetran.br_pg_ms_cns_stores_ms_public.geo_queue_store gqs
  INNER JOIN fivetran.br_pg_ms_cns_stores_ms_public.geo_queue_point_configuration c
         ON gqs.point_id = c.point_id
  LEFT JOIN fivetran.br_pg_ms_cns_stores_ms_public.geo_queue_point gqp
         ON gqs.point_id = gqp.point_id
  WHERE COALESCE(gqs._fivetran_deleted, FALSE) = FALSE
    AND COALESCE(gqp._fivetran_deleted, FALSE) = FALSE
)
-- pegar config vigente no momento do pedido:
LEFT JOIN br_store_geoqueue g
       ON go.country = g.country
      AND go.store_id = g.store_id
      AND g.geo_created_at <= go.created_at
QUALIFY ROW_NUMBER() OVER (PARTITION BY go.order_id
                           ORDER BY g.geo_created_at DESC) = 1
```
> **Armadilha:** sem o QUALIFY + filtro temporal, o pedido pode pegar a geoqueue errada.

---

## 7. Playbooks operacionais

### 7.1 GeoQueues
- **Métricas:** `orders_adoption` (RT in queue ou trusted), `orders_adoption_acido` (estritamente in queue), cancel por geoqueue (≤3%), Orders in Queue SP (≥80%).
- **Tipos:** `clã`, `Without Geo`, `Turbo Rest`, `Turbo Retail/Mixto`.
- **Ativação:** (1) zonas com cancel RT > target via `flows/on_demand/geoqueues_analysis/`; (2) adoption por zone; (3) acionar @julia.nasser; (4) monitorar D+1 e D+7.

### 7.2 Turbo
- Vertical <15min. Métricas próprias: pre-picking, RT wait in store (maior), Assign RT.
- Cancel Turbo entra na meta de 3% (junto com Retail).
- `group_andina='TURBO'` ↔ vertical_sub_group='TURBO'; `'TURBO_REST'` ↔ 'TURBO_RESTAURANTS'.

### 7.3 Incentivos (ver `incentive_simulation_spec.md`)
| Tipo | Quando | Stack com On Top | Stack com Missions | Stack com Guarantees |
|---|---|---|---|---|
| On Top (QR) | crise imediata | — | ❌ | ✅ |
| Missions (3 tiers) | produtividade mid/high | ❌ | — | ❌ |
| Guarantees (3 tiers) | confiabilidade supply | ✅ | ❌ | — |

**Guardrails:**
- On Top > 30% base EPO → flag
- Mission win rate top tier > 15% → flag (target ~10%)
- Guarantee paid rate > 15% → flag (target 7–15%)
- Tiers não progressivos → flag

---

## 8. Escalation Protocol

| Condição | Ação | Owner |
|---|---|---|
| Cancel Retail > 4% em SP ou RJ | Analisar risco da semana | @natalia.pavin (FP&A) |
| Cancel c/ Turbo > 3% em SP ou RJ | Analisar risco da semana | @natalia.pavin |
| Cancel Retail SP > 5% por 2+ dias | Plano de incentivo emergencial | @bruna.piccinin (Supply) |
| Cancel Partner > 1.5% (ou 2%) | Diagnóstico stockout/lojas fechadas | @martin.alvarado (Partner Ops) |
| Cancel GeoQueue > 3% | Revisar engajamento | @julia.nasser (Engagement) |
| GeoQueue adoption SP < 70% | Revisar configuração de queues | @julia.nasser |
| Acceptance Rate < 70% | Revisar táticos de frota | @julia.nasser |
| OL > 1.5% em 2+ semanas | Análise estrutural | @natalia.pavin |

---

## 9. Deep Dive — Cancel Retail BR (D-1)

**Persona:** Senior Strategic Ops Manager + Lead Data Analyst.

**6 blocos de análise:**
1. **Geo/Categórica** — cancel rate por sub-vertical, cidade/zona, janela horária, GeoQueue.
2. **Comparativos** — D-1 vs D-2 e D-8; D-1 vs D-15/22/29.
3. **Fatores externos** — zonas com deterioração > 2pp vs histórico.
4. **Frota** — Acceptance por cidade × tipo RT (Clan vs Nuvem), EPH, Release Rate, Orders Without Prospect.
5. **Incentivos** — base incentivada vs não, regras de empilhamento.
6. **Algoritmo** — Notification Rate, GeoQueue Adoption, Auto-acceptance.

**Estrutura da resposta:** Executive Summary → Root Cause Analysis → Anomalias → Action Plan (com owners).

---

## 10. Daily Report Retail — Cancel Rate Slack

**Fonte:** `RP_SILVER_DB_PROD.OPS_BR.BR_ORDERS_PBI`, filtro `VERTICAL='RETAIL'`, data via `DAY`.

**Estrutura:** msg principal D-1 vs D-8 + bloco WTD vs LW + Reply 1 (subvertical) + Reply 2 (top 15 brands com `canceled_orders ≥ 10`).

**Semáforos:** 🟢 <5% · 🟡 5–8% · 🔴 >8%. Variação: ✅ melhora / 🔴 piora / ➡️ estável. **Principal Ofensor** = `MODE(SUBCATEGORY)` entre cancelados.

**Brand:** usar campo consolidado `BRAND` (já é COALESCE), nunca `BRAND_NAME`/`BRAND_GROUP_NAME` crus.

**Slack:** teste `G01GF7M4C91` · prod `C0B0UCKK54L`.

---

## 11. Snippets

### Janela 7 dias completos (exclui dia corrente)
```sql
WHERE created_at >= DATEADD(day, -7, CURRENT_DATE())
  AND created_at <  CURRENT_DATE()
```

### Pedidos cancelados por stockout (CSO)
```sql
SELECT DISTINCT order_id
FROM rp_silver_db_prod.cpgs_local_analytics.tbl_tipificador_br
WHERE final_state = 'cso'
  AND day >= DATEADD(day, -7, CURRENT_DATE())
  AND day <  CURRENT_DATE()
```
> `FINAL_STATE` é do **pedido**; `STOCKOUT`/`SUBSTITUTED` (0/1) são do **produto**.

### Stockout × Security Stock (fonte da verdade)
> ⚠️ Sempre cruzar com `PRODUCT_AVAILABILITY` — `product_store_security_stock` da tipificador não é 100% confiável.
```sql
LEFT JOIN fivetran.br_amysql_cpgs_clg_im_cpgs_clg_inventory_manager.product_availability pa
       ON t.product_id = pa.id
      AND pa.entity = 'retailerProduct'
      AND pa.country = 'BR'
      AND pa._fivetran_deleted = FALSE
```

### Timeline de um pedido
```sql
SELECT om.created_at, om.type, om.storekeeper_id, om.cms_user_id, om.params
FROM fivetran.br_core_orders_public.order_modifications om
WHERE om.order_id = :order_id
  AND om._fivetran_deleted = FALSE
ORDER BY om.created_at;
```

---

## 12. Owners

| Área | Owner | Slack |
|---|---|---|
| Supply | Bruna Piccinin | @bruna.piccinin |
| Engagement / GeoQueues | Julia Nasser | @julia.nasser |
| Partner Ops | Martin Alvarado | @martin.alvarado |
| FP&A | Natalia Pavin | @natalia.pavin |

---

## 13. Pendências

1. Confirmar `TBL_TIPIFICADOR_BR.CP_PHYSICAL_ID` ↔ `TBL_DIM_STORES.CP_STORE_ID`.
2. Listar valores possíveis de `PRODUCT_AVAILABILITY.ENTITY`.
3. Validar cast TEXT→NUMBER em IDs de `BR_NEW_CATALOG_INTEGRATION_METRICS_METADATA`.
4. Preencher playbook **Núvem** (descrição, métricas-chave, ativação).
