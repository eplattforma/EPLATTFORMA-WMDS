from app import db


class DwRecoBasket(db.Model):
    """
    Market basket rules:
    - from_item_code => to_item_code
    - support, confidence, lift
    """
    __tablename__ = "dw_reco_basket"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    from_item_code = db.Column(db.String, nullable=False, index=True)
    to_item_code = db.Column(db.String, nullable=False, index=True)

    support = db.Column(db.Float, nullable=False)
    confidence = db.Column(db.Float, nullable=False)
    lift = db.Column(db.Float, nullable=True)


class DwCategoryPenetration(db.Model):
    """
    Customer x Category matrix:
    - total_spend and has_category flag (0/1)
    """
    __tablename__ = "dw_category_penetration"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_code_365 = db.Column(db.String, nullable=False, index=True)
    category_code = db.Column(db.String, nullable=False, index=True)

    total_spend = db.Column(db.Numeric(12, 2), nullable=False)
    has_category = db.Column(db.Integer, nullable=False)


class DwShareOfWallet(db.Model):
    """
    Per customer:
    - actual total spend
    - global average spend
    - opportunity_gap = max(avg_spend - actual_spend, 0)
    """
    __tablename__ = "dw_share_of_wallet"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_code_365 = db.Column(db.String, nullable=False, unique=True)

    actual_spend = db.Column(db.Numeric(14, 2), nullable=False)
    avg_spend = db.Column(db.Numeric(14, 2), nullable=False)
    opportunity_gap = db.Column(db.Numeric(14, 2), nullable=False)


class DwChurnRisk(db.Model):
    """
    Churn risk by customer & category:
    - compares recent vs previous period spend
    """
    __tablename__ = "dw_churn_risk"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_code_365 = db.Column(db.String, nullable=False, index=True)
    category_code = db.Column(db.String, nullable=False, index=True)

    recent_spend = db.Column(db.Numeric(14, 2), nullable=False)
    prev_spend = db.Column(db.Numeric(14, 2), nullable=False)
    spend_ratio = db.Column(db.Float, nullable=False)
    drop_pct = db.Column(db.Float, nullable=False)
    churn_flag = db.Column(db.Integer, nullable=False)
