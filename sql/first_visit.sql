-- 新用戶表：所有 first_visit (首次造訪) 使用者 + 後續轉換旅程標記
-- fv  = 每位 user_pseudo_id 的首訪事件 (含維度)
-- pur = 每位 user_pseudo_id 的首次購買 (最早一筆 purchase 與彙總金額)
-- 以 LEFT JOIN 保留所有新用戶，未購買者轉換欄位為 NULL。
-- 參數：${START} / ${END} 為 _TABLE_SUFFIX 日期區間 (YYYYMMDD，含頭含尾)
WITH fv AS (
    SELECT
        user_pseudo_id,
        PARSE_DATE('%Y%m%d', event_date)        AS first_visit_date,
        TIMESTAMP_MICROS(event_timestamp)       AS first_visit_datetime,
        geo.country                              AS country,
        geo.region                               AS region,
        geo.city                                 AS city,
        geo.continent                            AS continent,
        device.category                          AS device_type,
        device.operating_system                  AS os,
        device.web_info.browser                  AS browser,
        device.language                          AS device_language,
        device.mobile_brand_name                 AS mobile_brand,
        traffic_source.name                      AS campaign_name,
        traffic_source.medium                    AS traffic_medium,
        traffic_source.source                    AS traffic_source,
        ROW_NUMBER() OVER (
            PARTITION BY user_pseudo_id
            ORDER BY event_timestamp
        )                                        AS rn
    FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '${START}' AND '${END}'
      AND event_name = 'first_visit'
),
pur AS (
    SELECT
        user_pseudo_id,
        MIN(TIMESTAMP_MICROS(event_timestamp))   AS first_purchase_datetime,
        SUM(ecommerce.purchase_revenue_in_usd)   AS purchase_revenue_total,
        COUNT(DISTINCT ecommerce.transaction_id) AS purchase_count
    FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '${START}' AND '${END}'
      AND event_name = 'purchase'
    GROUP BY user_pseudo_id
)
SELECT
    fv.user_pseudo_id,
    fv.first_visit_date,
    fv.first_visit_datetime,
    fv.country, fv.region, fv.city, fv.continent,
    fv.device_type, fv.os, fv.browser, fv.device_language, fv.mobile_brand,
    fv.campaign_name, fv.traffic_medium, fv.traffic_source,
    -- 轉換旅程標記
    (pur.user_pseudo_id IS NOT NULL)                       AS did_purchase,
    pur.first_purchase_datetime,
    pur.purchase_revenue_total,
    COALESCE(pur.purchase_count, 0)                        AS purchase_count,
    DATE_DIFF(DATE(pur.first_purchase_datetime),
              fv.first_visit_date, DAY)                    AS days_to_first_purchase
FROM fv
LEFT JOIN pur USING (user_pseudo_id)
WHERE fv.rn = 1
