from fastapi import FastAPI, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Optional
import os

from database import engine, Base, get_db
import models

app = FastAPI(
    title="미니 ERP (Mini ERP)",
    description="FastAPI + MySQL + Jinja2 기반의 통합 ERP 스켈레톤",
    version="1.0.0"
)

# 데이터베이스 테이블 자동 생성 (서버 시작시)
try:
    Base.metadata.create_all(bind=engine)
    print("Database tables initialized successfully.")
except Exception as e:
    print(f"Warning: Database initialization deferred (Check MySQL connection): {e}")

# 정적 파일 및 HTML 템플릿 설정
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.globals["int"] = int



# ==========================================
# 1. 대시보드 (Dashboard)
# ==========================================
@app.get("/", response_class=HTMLResponse)
def read_dashboard(request: Request, db: Session = Depends(get_db)):
    from collections import defaultdict

    try:
        customers = db.query(models.Customer).all()
        products = db.query(models.Product).all()
        orders = db.query(models.Order).all()

        completed_statuses = ["완료", "COMPLETED", "주문완료"]
        canceled_statuses = ["취소", "CANCELED", "CANCELLED"]

        # 1. 총 매출 (완료된 주문 금액 합계)
        total_revenue = sum(
            int(o.product.price * o.quantity)
            for o in orders if o.status in completed_statuses and o.product
        )

        # 2. 총 주문 건수 (취소 제외)
        active_orders = [o for o in orders if o.status not in canceled_statuses]
        total_order_count = len(active_orders)

        # 3. 총 고객 수
        total_customer_count = len(customers)

        # 4. 재고 부족 상품 수 (10개 이하)
        low_stock_products = [p for p in products if p.stock <= 10]
        low_stock_count = len(low_stock_products)

        # 5. 날짜별 매출 (완료 주문)
        sales_by_date_dict = defaultdict(int)
        for o in orders:
            if o.status in completed_statuses and o.product:
                date_str = o.created_at.strftime("%Y-%m-%d") if o.created_at else "기타"
                sales_by_date_dict[date_str] += int(o.product.price * o.quantity)

        sales_by_date = [
            {"date": d, "revenue": rev}
            for d, rev in sorted(sales_by_date_dict.items())
        ]

        # 6. 많이 팔린 상품 TOP 5 (취소 제외)
        product_sales_dict = defaultdict(int)
        for o in orders:
            if o.status not in canceled_statuses and o.product:
                product_sales_dict[o.product.name] += o.quantity

        top5_products = [
            {"name": name, "quantity": qty}
            for name, qty in sorted(product_sales_dict.items(), key=lambda x: x[1], reverse=True)[:5]
        ]

        # 7. 주문 상태별 개수 (대기 / 완료 / 취소)
        status_counts = {"대기": 0, "완료": 0, "취소": 0}
        for o in orders:
            if o.status in ["대기", "PENDING"]:
                status_counts["대기"] += 1
            elif o.status in ["완료", "COMPLETED", "주문완료"]:
                status_counts["완료"] += 1
            elif o.status in ["취소", "CANCELED", "CANCELLED"]:
                status_counts["취소"] += 1
            else:
                status_counts[o.status] = status_counts.get(o.status, 0) + 1

        recent_orders = (
            db.query(models.Order)
            .order_by(models.Order.id.desc())
            .limit(5)
            .all()
        )
        db_connected = True
    except Exception as e:
        total_revenue = 0
        total_order_count = 0
        total_customer_count = 0
        low_stock_count = 0
        sales_by_date = []
        top5_products = []
        status_counts = {"대기": 0, "완료": 0, "취소": 0}
        recent_orders = []
        low_stock_products = []
        db_connected = False
        print(f"DB Error on dashboard: {e}")

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "total_revenue": total_revenue,
            "total_order_count": total_order_count,
            "total_customer_count": total_customer_count,
            "low_stock_count": low_stock_count,
            "sales_by_date": sales_by_date,
            "top5_products": top5_products,
            "status_counts": status_counts,
            "recent_orders": recent_orders,
            "low_stock_products": low_stock_products,
            "db_connected": db_connected,
            "active_page": "dashboard"
        }
    )


# ==========================================
# 2. 고객 관리 (Customers)
# ==========================================
@app.get("/customers", response_class=HTMLResponse)
def list_customers(
    request: Request,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db)
):
    customers = db.query(models.Customer).order_by(models.Customer.id.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="customers.html",
        context={
            "customers": customers,
            "error": error,
            "msg": msg,
            "active_page": "customers"
        }
    )

@app.post("/customers")
def create_customer(
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    # 이메일 중복 체크
    existing = db.query(models.Customer).filter(models.Customer.email == email).first()
    if existing:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/customers?error={quote('이미 등록된 이메일이에요')}",
            status_code=303
        )

    customer = models.Customer(name=name, email=email, phone=phone)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/customers?msg={quote('고객이 등록되었습니다.')}",
        status_code=303
    )

@app.post("/customers/{customer_id}/edit")
def edit_customer(
    customer_id: int,
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/customers?error={quote('존재하지 않는 고객입니다.')}",
            status_code=303
        )

    # 이메일 중복 체크 (자기 자신 제외)
    existing_email = db.query(models.Customer).filter(
        models.Customer.email == email,
        models.Customer.id != customer_id
    ).first()
    if existing_email:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/customers?error={quote('이미 등록된 이메일이에요')}",
            status_code=303
        )

    customer.name = name
    customer.email = email
    customer.phone = phone
    db.commit()
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/customers?msg={quote('고객 정보가 수정되었습니다.')}",
        status_code=303
    )

@app.post("/customers/{customer_id}/delete")
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/customers?error={quote('존재하지 않는 고객입니다.')}",
            status_code=303
        )

    # 주문 내역 유무 확인
    order_count = db.query(models.Order).filter(models.Order.customer_id == customer_id).count()
    if order_count > 0:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/customers?error={quote('주문 내역이 있어 삭제할 수 없어요')}",
            status_code=303
        )

    db.delete(customer)
    db.commit()
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/customers?msg={quote('고객 정보가 삭제되었습니다.')}",
        status_code=303
    )


# ==========================================
# 3. 상품/재고 관리 (Products)
# ==========================================
@app.get("/products", response_class=HTMLResponse)
def list_products(
    request: Request,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db)
):
    products = db.query(models.Product).order_by(models.Product.id.desc()).all()
    low_stock_count = sum(1 for p in products if p.stock <= 10)
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={
            "products": products,
            "low_stock_count": low_stock_count,
            "error": error,
            "msg": msg,
            "active_page": "products"
        }
    )

@app.post("/products")
def create_product(
    name: str = Form(...),
    price: int = Form(...),
    stock: int = Form(...),
    db: Session = Depends(get_db)
):
    # 가격 및 재고 음수 검증
    if price < 0 or stock < 0:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/products?error={quote('가격과 재고는 마이너스가 될 수 없어요')}",
            status_code=303
        )

    product = models.Product(name=name, price=price, stock=stock)
    db.add(product)
    db.commit()
    db.refresh(product)
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/products?msg={quote('상품이 등록되었습니다.')}",
        status_code=303
    )

@app.post("/products/{product_id}/edit")
def edit_product(
    product_id: int,
    name: str = Form(...),
    price: int = Form(...),
    stock: int = Form(...),
    db: Session = Depends(get_db)
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/products?error={quote('존재하지 않는 상품입니다.')}",
            status_code=303
        )

    # 가격 및 재고 음수 검증
    if price < 0 or stock < 0:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/products?error={quote('가격과 재고는 마이너스가 될 수 없어요')}",
            status_code=303
        )

    product.name = name
    product.price = price
    product.stock = stock
    db.commit()
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/products?msg={quote('상품 정보가 수정되었습니다.')}",
        status_code=303
    )

@app.post("/products/{product_id}/stock")
def adjust_stock(
    product_id: int,
    action: str = Form(...),
    db: Session = Depends(get_db)
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/products?error={quote('존재하지 않는 상품입니다.')}",
            status_code=303
        )

    if action == "increase":
        product.stock += 1
    elif action == "decrease":
        if product.stock <= 0:
            from urllib.parse import quote
            return RedirectResponse(
                url=f"/products?error={quote('재고는 마이너스가 될 수 없어요')}",
                status_code=303
            )
        product.stock -= 1

    db.commit()
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/products?msg={quote('재고 수량이 조정되었습니다.')}",
        status_code=303
    )

@app.post("/products/{product_id}/delete")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/products?error={quote('존재하지 않는 상품입니다.')}",
            status_code=303
        )

    # 주문 내역 유무 확인
    order_count = db.query(models.Order).filter(models.Order.product_id == product_id).count()
    if order_count > 0:
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/products?error={quote('주문 내역이 있어 삭제할 수 없어요')}",
            status_code=303
        )

    db.delete(product)
    db.commit()
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/products?msg={quote('상품이 삭제되었습니다.')}",
        status_code=303
    )


# ==========================================
# 4. 주문 관리 (Orders)
# ==========================================
@app.get("/orders", response_class=HTMLResponse)
def list_orders(
    request: Request,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db)
):
    orders = db.query(models.Order).order_by(models.Order.id.desc()).all()
    customers = db.query(models.Customer).all()
    products = db.query(models.Product).all()
    
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={
            "orders": orders,
            "customers": customers,
            "products": products,
            "error": error,
            "msg": msg,
            "active_page": "orders"
        }
    )

@app.post("/orders")
def create_order(
    customer_id: int = Form(...),
    product_id: int = Form(...),
    quantity: int = Form(...),
    status: str = Form("완료"),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote

    if quantity <= 0:
        return RedirectResponse(
            url=f"/orders?error={quote('주문 수량은 1개 이상이어야 해요')}",
            status_code=303
        )

    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        return RedirectResponse(
            url=f"/orders?error={quote('존재하지 않는 고객입니다.')}",
            status_code=303
        )

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        return RedirectResponse(
            url=f"/orders?error={quote('존재하지 않는 상품입니다.')}",
            status_code=303
        )

    # 재고 부족 검증
    if quantity > product.stock:
        return RedirectResponse(
            url=f"/orders?error={quote('재고가 부족해요')}",
            status_code=303
        )

    # 재고 자동 감축
    product.stock -= quantity
    order = models.Order(
        customer_id=customer_id,
        product_id=product_id,
        quantity=quantity,
        status=status
    )
    db.add(order)
    db.commit()
    return RedirectResponse(
        url=f"/orders?msg={quote('주문이 정상적으로 접수되었습니다.')}",
        status_code=303
    )

@app.post("/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote

    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        return RedirectResponse(
            url=f"/orders?error={quote('존재하지 않는 주문입니다.')}",
            status_code=303
        )

    old_status = order.status
    new_status = status

    if old_status == new_status:
        return RedirectResponse(url="/orders", status_code=303)

    product = order.product

    # 1. 기존 상태가 취소가 아니었다가 -> '취소'로 변경 시: 재고 복구 (수량 반환)
    if old_status != "취소" and new_status == "취소":
        if product:
            product.stock += order.quantity

    # 2. 기존 상태가 '취소'였다가 -> '완료'나 '대기'로 복구 시: 재고 다시 차감
    elif old_status == "취소" and new_status != "취소":
        if product:
            if product.stock < order.quantity:
                return RedirectResponse(
                    url=f"/orders?error={quote('재고가 부족하여 주문을 복구할 수 없어요')}",
                    status_code=303
                )
            product.stock -= order.quantity

    order.status = new_status
    db.commit()
    return RedirectResponse(
        url=f"/orders?msg={quote('주문 상태가 변경되었습니다.')}",
        status_code=303
    )

@app.post("/orders/{order_id}/delete")
def delete_order(order_id: int, db: Session = Depends(get_db)):
    from urllib.parse import quote

    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order:
        # 미취소 주문 삭제 시 재고 원복
        if order.status != "취소" and order.product:
            order.product.stock += order.quantity
        db.delete(order)
        db.commit()
    return RedirectResponse(
        url=f"/orders?msg={quote('주문 내역이 삭제되었습니다.')}",
        status_code=303
    )


# ==========================================
# 5. REST API 샘플 (JSON 데이터 처리용)
# ==========================================
@app.get("/api/health")
def health_check():
    return {"status": "ok", "app": "Mini ERP FastAPI"}
