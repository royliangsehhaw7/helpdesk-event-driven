
-- ============================================================
-- Customer Service MAS — MySQL Schema
-- ============================================================

CREATE DATABASE IF NOT EXISTS customer_service
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE customer_service;

-- ------------------------------------------------------------
-- customers
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id            VARCHAR(20)   NOT NULL,
    name          VARCHAR(100)  NOT NULL,
    email         VARCHAR(150)  NOT NULL,
    tier          ENUM('standard','premium','vip') NOT NULL DEFAULT 'standard',
    joined_date   DATE          NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_customers_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- products
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id                VARCHAR(20)   NOT NULL,
    name              VARCHAR(150)  NOT NULL,
    category          VARCHAR(50)   NOT NULL,
    warranty_months   INT           NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_products_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- orders
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    order_id      VARCHAR(20)   NOT NULL,
    customer_id   VARCHAR(20)   NOT NULL,
    order_date    DATE          NOT NULL,
    status        ENUM('pending','delivered','cancelled') NOT NULL DEFAULT 'pending',
    PRIMARY KEY (order_id),
    KEY idx_orders_customer_id (customer_id),
    CONSTRAINT fk_orders_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id)
        ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- order_details
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_details (
    id            BIGINT        NOT NULL AUTO_INCREMENT,
    order_id      VARCHAR(20)   NOT NULL,
    product_id    VARCHAR(20)   NOT NULL,
    quantity      INT           NOT NULL DEFAULT 1,
    unit_price    DECIMAL(10,2) NOT NULL,
    total_price   DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_order_details_order_id (order_id),
    KEY idx_order_details_product_id (product_id),
    CONSTRAINT fk_order_details_order
        FOREIGN KEY (order_id) REFERENCES orders (order_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_order_details_product
        FOREIGN KEY (product_id) REFERENCES products (id)
        ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- complaint_history
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS complaint_history (
    complaint_id  VARCHAR(20)   NOT NULL,
    customer_id   VARCHAR(20)   NOT NULL,
    order_id      VARCHAR(20)   NOT NULL,
    date          DATE          NOT NULL,
    type          ENUM('packaging','product','delivery','billing') NOT NULL,
    resolution    ENUM('refunded','replaced','rejected','pending') NOT NULL DEFAULT 'pending',
    PRIMARY KEY (complaint_id),
    KEY idx_complaint_history_customer_id (customer_id),
    KEY idx_complaint_history_order_id (order_id),
    CONSTRAINT fk_complaint_history_customer
        FOREIGN KEY (customer_id) REFERENCES customers (id)
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_complaint_history_order
        FOREIGN KEY (order_id) REFERENCES orders (order_id)
        ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- policy
-- Single-row config table. Always query WHERE id = 1.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy (
    id                                INT  NOT NULL DEFAULT 1,
    refund_window_days                INT  NOT NULL DEFAULT 30,
    vip_extended_refund_days          INT  NOT NULL DEFAULT 30,
    premium_extended_refund_days      INT  NOT NULL DEFAULT 15,
    complaint_escalation_threshold    INT  NOT NULL DEFAULT 3,
    replacement_eligible_categories   JSON NOT NULL,
    auto_refund_complaint_types       JSON NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT chk_policy_single_row CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;



-- ============================================================
-- Seed data
-- ============================================================

INSERT INTO customers (id, name, email, tier, joined_date) VALUES
    ('C001', 'Aisha Rahman', 'aisha.rahman@email.com', 'premium',  '2021-06-10'),
    ('C002', 'Marcus Webb',  'marcus.webb@email.com',  'standard', '2023-01-22'),
    ('C003', 'Priya Nair',   'priya.nair@email.com',   'vip',      '2019-11-05');

INSERT INTO products (id, name, category, warranty_months) VALUES
    ('P001', 'Wireless Ergonomic Keyboard', 'electronics',   12),
    ('P002', 'Organic Loose Leaf Tea Set',  'food_beverage',  0),
    ('P003', 'Stainless Steel Cookware Set','kitchenware',   24);

INSERT INTO orders (order_id, customer_id, order_date, status) VALUES
    ('ORD-1001', 'C001', '2026-04-10', 'delivered'),
    ('ORD-1002', 'C002', '2026-03-01', 'delivered'),
    ('ORD-1003', 'C003', '2026-05-01', 'delivered');

INSERT INTO order_details (order_id, product_id, quantity, unit_price, total_price) VALUES
    ('ORD-1001', 'P001', 1, 149.90, 149.90),
    ('ORD-1002', 'P002', 2,  39.95,  79.90),
    ('ORD-1003', 'P003', 1, 299.00, 299.00);

INSERT INTO complaint_history
    (complaint_id, customer_id, order_id, date, type, resolution) VALUES
    ('CMP-001', 'C001', 'ORD-1001', '2025-11-20', 'delivery',  'refunded'),
    ('CMP-002', 'C003', 'ORD-1003', '2026-01-14', 'packaging', 'replaced');

INSERT INTO policy (
    id,
    refund_window_days,
    vip_extended_refund_days,
    premium_extended_refund_days,
    complaint_escalation_threshold,
    replacement_eligible_categories,
    auto_refund_complaint_types
) VALUES (
    1, 30, 30, 15, 3,
    '["electronics","kitchenware"]',
    '["packaging","billing"]'
);