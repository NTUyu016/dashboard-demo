SELECT 
    -- 關聯外鍵：用來跟事件表串接
    PARSE_DATE('%Y%m%d', event_date) as event_date,

    user_pseudo_id,
    
    TIMESTAMP_MICROS(event_timestamp) as event_datetime,
    event_name,
    
    -- 🌟 產品核心維度 (對照：採購料號與品名)
    i.item_id as item_id,                        -- 產品 ID (料號)
    i.item_name as item_name,                    -- 產品名稱 (品名)
    i.item_brand as item_brand,                  -- 品牌 (供應商商標)
    i.item_variant as item_variant,              -- 型號/規格
    
    -- 🌟 產品分類階層 (對照：採購品類類別階層)
    i.item_category as item_category,            -- 主分類 (如：機台設備)
    i.item_category2 as item_category2,          -- 次分類 (如：光刻機零組件)
    i.item_category3 as item_category3,          -- 三級分類
    i.item_category4 as item_category4,
    i.item_category5 as item_category5,
    
    -- 🌟 財務指標 (對照：採購單價、數量、經費)
    i.price as item_price,                       -- 產品單價 (幣別通常預設為 USD)
    i.quantity as item_quantity,                 -- 購買數量
    (i.price * i.quantity) as item_total_revenue,-- 該品項小計總額
    i.item_revenue as item_reported_revenue,     -- 官方申報營收
    
    -- 🌟 行銷與促銷內容 (對照：採購專案代碼、合約折讓)
    i.item_refund as item_refund_amount,         -- 退款金額
    i.coupon as item_coupon_code,                -- 優惠券 (合約折扣碼)
    i.affiliation as store_affiliation,          -- 商店歸屬 (供應商分公司)
    i.location_id as location_id                 -- 庫房/貨架位置 ID
    
FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
CROSS JOIN UNNEST(items) AS i
WHERE _TABLE_SUFFIX = '20210101'