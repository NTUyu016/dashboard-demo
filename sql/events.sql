-- 事件表：GA4 obfuscated sample ecommerce 的逐事件明細
-- 參數：${START} / ${END} 為 _TABLE_SUFFIX 日期區間 (YYYYMMDD，含頭含尾)
SELECT
    PARSE_DATE('%Y%m%d', event_date)            AS event_date,
    TIMESTAMP_MICROS(event_timestamp)           AS event_datetime,
    event_name,
    event_value_in_usd,
    user_pseudo_id,
    geo.country                                  AS country,
    geo.region                                   AS region,
    geo.city                                     AS city,
    geo.sub_continent                            AS sub_continent,
    geo.continent                                AS continent,
    device.category                              AS device_type,
    device.operating_system                      AS os,
    device.web_info.browser                      AS browser,
    device.language                              AS device_language,
    device.mobile_brand_name                     AS mobile_brand,
    traffic_source.name                          AS campaign_name,
    traffic_source.medium                        AS traffic_medium,
    traffic_source.source                        AS traffic_source,
    ecommerce.total_item_quantity                AS total_items_count,
    ecommerce.purchase_revenue_in_usd            AS total_purchase_revenue,
    ecommerce.transaction_id                     AS transaction_id,
    ecommerce.unique_items                        AS unique_items_count
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX BETWEEN '${START}' AND '${END}'
