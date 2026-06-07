SELECT 
    -- 🌟 1. 時間維度 (時序分析、分區核心)
    PARSE_DATE('%Y%m%d', event_date) as event_date,
    TIMESTAMP_MICROS(event_timestamp) as event_datetime,
    event_name,
    event_previous_timestamp,
    event_value_in_usd,                         -- 該事件折合美金價值
    
    -- 🌟 2. 使用者身分識別
    user_pseudo_id,                             -- 設備識別碼 (訪客ID)
    user_first_touch_timestamp,                 -- 首次觸發時間
    
    -- 🌟 3. 欄式優化：地理與廠區維度 (對照：全球物料來源廠區)
    geo.country as country,
    geo.region as region,                       -- 州/省 (如：台灣、加州)
    geo.city as city,                           -- 城市 (如：新竹、台南)
    geo.sub_continent as sub_continent,        -- 次大陸 (如：東亞)
    geo.continent as continent,                 -- 大陸 (如：亞洲)
    geo.metro as metro_area,                    -- 地鐵都會區
    
    -- 🌟 4. 欄式優化：裝置與硬體環境 (對照：機台/終端設備類別)
    device.category as device_type,             -- 裝置類別 (desktop/mobile)
    device.operating_system as os,              -- 作業系統 (Windows/iOS)
    device.operating_system_version as os_version,
    device.web_info.browser as browser,         -- 瀏覽器
    device.web_info.browser_version as browser_version,
    device.language as device_language,         -- 語系設定
    device.mobile_brand_name as mobile_brand,   -- 手機品牌
    device.mobile_model_name as mobile_model,   -- 手機型號
    
    -- 🌟 5. 欄式優化：流量渠道來源 (對照：採購單進件管道/簽核媒介)
    traffic_source.name as campaign_name,       -- 行銷活動名稱
    traffic_source.medium as traffic_medium,    -- 媒介 (organic/cpc)
    traffic_source.source as traffic_source,    -- 來源 (google/direct)
    
    -- 🌟 6. 整體電商加總維度 (免炸開，直接抓取事件層級加總)
    ecommerce.total_item_quantity as total_items_count, -- 該次事件購買的商品總件數
    ecommerce.purchase_revenue_in_usd as total_purchase_revenue, -- 該次結帳總美金金額
    ecommerce.transaction_id as transaction_id, -- 交易訂單 ID (採購單號 PO Number)
    ecommerce.unique_items as unique_items_count -- 該次結帳不重複的品項數

FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
WHERE _TABLE_SUFFIX = '20210101'