-- 商品表：把每筆事件的 items 陣列展開成逐商品明細 (已購買/瀏覽等)
-- 參數：${START} / ${END} 為 _TABLE_SUFFIX 日期區間 (YYYYMMDD，含頭含尾)
SELECT
    PARSE_DATE('%Y%m%d', event_date)            AS event_date,
    user_pseudo_id,
    TIMESTAMP_MICROS(event_timestamp)           AS event_datetime,
    event_name,
    i.item_id                                    AS item_id,
    i.item_name                                  AS item_name,
    i.item_brand                                 AS item_brand,
    i.item_variant                               AS item_variant,
    i.item_category                              AS item_category,
    i.item_category2                             AS item_category2,
    i.price                                       AS item_price,
    i.quantity                                    AS item_quantity,
    (i.price * i.quantity)                       AS item_total_revenue,
    i.item_revenue                                AS item_reported_revenue,
    i.coupon                                      AS item_coupon_code,
    i.affiliation                                 AS store_affiliation,
    i.location_id                                 AS location_id
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
CROSS JOIN UNNEST(items) AS i
WHERE _TABLE_SUFFIX BETWEEN '${START}' AND '${END}'
