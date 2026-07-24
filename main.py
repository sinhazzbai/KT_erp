from fastapi import FastAPI, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
import hashlib
import os

from database import engine, Base, get_db
import models

app = FastAPI(
    title="미니 ERP (Mini ERP)",
    description="FastAPI + MySQL + Jinja2 기반의 통합 ERP 스켈레톤",
    version="1.0.0"
)

from starlette.middleware.base import BaseHTTPMiddleware

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ["/login", "/api/health"] or path.startswith("/static"):
            return await call_next(request)

        username = request.session.get("username")
        if not username:
            from urllib.parse import quote
            return RedirectResponse(
                url=f"/login?error={quote('로그인이 필요합니다.')}",
                status_code=303
            )

        return await call_next(request)

# 미들웨어 추가 (역순으로 실행되므로 SessionMiddleware가 먼저 실행되어 session을 구성함)
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key="mini_erp_secure_secret_key_2026")

# 정적 파일 및 HTML 템플릿 설정
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.globals["int"] = int


def hash_password(password: str) -> str:
    """비밀번호 안전 해싱 (PBKDF2-HMAC-SHA256)"""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"mini_erp_salt_2026", 100000).hex()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """비밀번호 검증"""
    return hash_password(plain_password) == hashed_password


# 데이터베이스 테이블 자동 생성 및 기본 관리자/일반사용자 계정 시딩
try:
    Base.metadata.create_all(bind=engine)
    print("Database tables initialized successfully.")
    
    # 기본 계정 자동 생성 (없을 경우만)
    from database import SessionLocal
    db = SessionLocal()
    if db.query(models.User).count() == 0:
        admin_user = models.User(
            username="admin",
            password_hash=hash_password("admin123"),
            role="admin"
        )
        normal_user = models.User(
            username="user1",
            password_hash=hash_password("user123"),
            role="user"
        )
        db.add(admin_user)
        db.add(normal_user)
        db.commit()
        print("Default accounts created: admin/admin123, user1/user123")
    db.close()
except Exception as e:
    print(f"Warning: Database initialization deferred (Check MySQL connection): {e}")


def get_current_user(request: Request, db: Session) -> Optional[models.User]:
    """현재 로그인된 유저 객체 반환"""
    username = request.session.get("username")
    if not username:
        return None
    return db.query(models.User).filter(models.User.username == username).first()


# ==========================================
# 0. 로그인 및 인증 (Login / Logout)
# ==========================================
@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    error: Optional[str] = None,
    msg: Optional[str] = None
):
    # 이미 로그인된 경우 대시보드로 이동
    if request.session.get("username"):
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error, "msg": msg}
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote

    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        return RedirectResponse(
            url=f"/login?error={quote('아이디 또는 비밀번호가 바르지 않습니다.')}",
            status_code=303
        )

    now = datetime.utcnow()

    # 1. 5회 이상 오답으로 인한 1분 잠금 상태 체크
    if user.lockout_until and now < user.lockout_until:
        remaining_seconds = int((user.lockout_until - now).total_seconds())
        return RedirectResponse(
            url=f"/login?error={quote(f'비밀번호 5회 이상 오답으로 계정이 잠겼습니다. 1분 후 (약 {remaining_seconds}초 뒤) 다시 시도해 주세요.')}",
            status_code=303
        )

    # 2. 1분이 경과했으면 잠금 해제 처리
    if user.lockout_until and now >= user.lockout_until:
        user.lockout_until = None
        user.failed_attempts = 0
        db.commit()

    # 3. 비밀번호 검증
    if verify_password(password, user.password_hash):
        # 성공 시 오답 횟수 및 잠금 초기화
        user.failed_attempts = 0
        user.lockout_until = None
        db.commit()

        # 세션에 정보 저장
        request.session["username"] = user.username
        request.session["role"] = user.role

        return RedirectResponse(url="/", status_code=303)
    else:
        # 실패 시 오답 횟수 증가
        user.failed_attempts += 1

        # 5회 이상 틀리면 1분간 계정 잠금 설정
        if user.failed_attempts >= 5:
            user.lockout_until = now + timedelta(minutes=1)
            db.commit()
            return RedirectResponse(
                url=f"/login?error={quote('암호가 5회 이상 틀렸습니다. 1분 후에 다시 시도해 주세요.')}",
                status_code=303
            )
        else:
            db.commit()
            return RedirectResponse(
                url=f"/login?error={quote(f'비밀번호가 올바르지 않습니다. (오류 {user.failed_attempts}/5회)')}",
                status_code=303
            )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    from urllib.parse import quote
    return RedirectResponse(
        url=f"/login?msg={quote('성공적으로 로그아웃되었습니다.')}",
        status_code=303
    )


# ==========================================
# 0-1. 계정 관리 (Users CRUD - 관리자 전용)
# ==========================================
@app.get("/users", response_class=HTMLResponse)
def list_users(
    request: Request,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        from urllib.parse import quote
        return RedirectResponse(
            url=f"/?error={quote('관리자만 계정 관리 페이지에 접속할 수 있습니다.')}",
            status_code=303
        )

    users = db.query(models.User).order_by(models.User.id.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context={
            "users": users,
            "current_user": current_user,
            "now": datetime.utcnow(),
            "error": error,
            "msg": msg,
            "active_page": "users"
        }
    )

@app.post("/users")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/?error={quote('관리자만 계정을 생성할 수 있습니다.')}",
            status_code=303
        )

    existing = db.query(models.User).filter(models.User.username == username).first()
    if existing:
        return RedirectResponse(
            url=f"/users?error={quote('이미 존재하는 아이디입니다.')}",
            status_code=303
        )

    new_user = models.User(
        username=username,
        password_hash=hash_password(password),
        role=role
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(
        url=f"/users?msg={quote('신규 계정이 생성되었습니다.')}",
        status_code=303
    )

@app.post("/users/{user_id}/edit")
def edit_user(
    request: Request,
    user_id: int,
    password: Optional[str] = Form(None),
    role: str = Form("user"),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/?error={quote('관리자만 계정을 수정할 수 있습니다.')}",
            status_code=303
        )

    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not target_user:
        return RedirectResponse(
            url=f"/users?error={quote('존재하지 않는 계정입니다.')}",
            status_code=303
        )

    if password and password.strip():
        target_user.password_hash = hash_password(password.strip())

    target_user.role = role
    db.commit()
    return RedirectResponse(
        url=f"/users?msg={quote('계정 정보가 수정되었습니다.')}",
        status_code=303
    )

@app.post("/users/{user_id}/unlock")
def unlock_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/?error={quote('관리자만 잠금을 해제할 수 있습니다.')}",
            status_code=303
        )

    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if target_user:
        target_user.failed_attempts = 0
        target_user.lockout_until = None
        db.commit()

    return RedirectResponse(
        url=f"/users?msg={quote('계정 잠금이 해제되었습니다.')}",
        status_code=303
    )

@app.post("/users/{user_id}/delete")
def delete_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/?error={quote('관리자만 계정을 삭제할 수 있습니다.')}",
            status_code=303
        )

    if current_user.id == user_id:
        return RedirectResponse(
            url=f"/users?error={quote('자기 자신 계정은 삭제할 수 없습니다.')}",
            status_code=303
        )

    target_user = db.query(models.User).filter(models.User.id == user_id).first()
    if target_user:
        db.delete(target_user)
        db.commit()

    return RedirectResponse(
        url=f"/users?msg={quote('계정이 삭제되었습니다.')}",
        status_code=303
    )


# ==========================================
# 1. 대시보드 (Dashboard)
# ==========================================
@app.get("/", response_class=HTMLResponse)
def read_dashboard(
    request: Request,
    error: Optional[str] = None,
    msg: Optional[str] = None,
    db: Session = Depends(get_db)
):
    from collections import defaultdict
    current_user = get_current_user(request, db)

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
            "current_user": current_user,
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
            "error": error,
            "msg": msg,
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
    current_user = get_current_user(request, db)
    customers = db.query(models.Customer).order_by(models.Customer.id.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="customers.html",
        context={
            "customers": customers,
            "current_user": current_user,
            "error": error,
            "msg": msg,
            "active_page": "customers"
        }
    )

@app.post("/customers")
def create_customer(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/customers?error={quote('관리자만 등록 권한이 있습니다.')}",
            status_code=303
        )

    # 이메일 중복 체크
    existing = db.query(models.Customer).filter(models.Customer.email == email).first()
    if existing:
        return RedirectResponse(
            url=f"/customers?error={quote('이미 등록된 이메일이에요')}",
            status_code=303
        )

    customer = models.Customer(name=name, email=email, phone=phone)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return RedirectResponse(
        url=f"/customers?msg={quote('고객이 등록되었습니다.')}",
        status_code=303
    )

@app.post("/customers/{customer_id}/edit")
def edit_customer(
    request: Request,
    customer_id: int,
    name: str = Form(...),
    email: str = Form(...),
    phone: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/customers?error={quote('관리자만 수정 권한이 있습니다.')}",
            status_code=303
        )

    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
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
        return RedirectResponse(
            url=f"/customers?error={quote('이미 등록된 이메일이에요')}",
            status_code=303
        )

    customer.name = name
    customer.email = email
    customer.phone = phone
    db.commit()
    return RedirectResponse(
        url=f"/customers?msg={quote('고객 정보가 수정되었습니다.')}",
        status_code=303
    )

@app.post("/customers/{customer_id}/delete")
def delete_customer(
    request: Request,
    customer_id: int,
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/customers?error={quote('관리자만 삭제 권한이 있습니다.')}",
            status_code=303
        )

    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        return RedirectResponse(
            url=f"/customers?error={quote('존재하지 않는 고객입니다.')}",
            status_code=303
        )

    # 주문 내역 유무 확인
    order_count = db.query(models.Order).filter(models.Order.customer_id == customer_id).count()
    if order_count > 0:
        return RedirectResponse(
            url=f"/customers?error={quote('주문 내역이 있어 삭제할 수 없어요')}",
            status_code=303
        )

    db.delete(customer)
    db.commit()
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
    current_user = get_current_user(request, db)
    products = db.query(models.Product).order_by(models.Product.id.desc()).all()
    low_stock_count = sum(1 for p in products if p.stock <= 10)
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={
            "products": products,
            "low_stock_count": low_stock_count,
            "current_user": current_user,
            "error": error,
            "msg": msg,
            "active_page": "products"
        }
    )

@app.post("/products")
def create_product(
    request: Request,
    name: str = Form(...),
    price: int = Form(...),
    stock: int = Form(...),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/products?error={quote('관리자만 등록 권한이 있습니다.')}",
            status_code=303
        )

    if price < 0 or stock < 0:
        return RedirectResponse(
            url=f"/products?error={quote('가격과 재고는 마이너스가 될 수 없어요')}",
            status_code=303
        )

    product = models.Product(name=name, price=price, stock=stock)
    db.add(product)
    db.commit()
    db.refresh(product)
    return RedirectResponse(
        url=f"/products?msg={quote('상품이 등록되었습니다.')}",
        status_code=303
    )

@app.post("/products/{product_id}/edit")
def edit_product(
    request: Request,
    product_id: int,
    name: str = Form(...),
    price: int = Form(...),
    stock: int = Form(...),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/products?error={quote('관리자만 수정 권한이 있습니다.')}",
            status_code=303
        )

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        return RedirectResponse(
            url=f"/products?error={quote('존재하지 않는 상품입니다.')}",
            status_code=303
        )

    if price < 0 or stock < 0:
        return RedirectResponse(
            url=f"/products?error={quote('가격과 재고는 마이너스가 될 수 없어요')}",
            status_code=303
        )

    product.name = name
    product.price = price
    product.stock = stock
    db.commit()
    return RedirectResponse(
        url=f"/products?msg={quote('상품 정보가 수정되었습니다.')}",
        status_code=303
    )

@app.post("/products/{product_id}/stock")
def adjust_stock(
    request: Request,
    product_id: int,
    action: str = Form(...),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/products?error={quote('관리자만 재고 조정 권한이 있습니다.')}",
            status_code=303
        )

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        return RedirectResponse(
            url=f"/products?error={quote('존재하지 않는 상품입니다.')}",
            status_code=303
        )

    if action == "increase":
        product.stock += 1
    elif action == "decrease":
        if product.stock <= 0:
            return RedirectResponse(
                url=f"/products?error={quote('재고는 마이너스가 될 수 없어요')}",
                status_code=303
            )
        product.stock -= 1

    db.commit()
    return RedirectResponse(
        url=f"/products?msg={quote('재고 수량이 조정되었습니다.')}",
        status_code=303
    )

@app.post("/products/{product_id}/delete")
def delete_product(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/products?error={quote('관리자만 삭제 권한이 있습니다.')}",
            status_code=303
        )

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        return RedirectResponse(
            url=f"/products?error={quote('존재하지 않는 상품입니다.')}",
            status_code=303
        )

    order_count = db.query(models.Order).filter(models.Order.product_id == product_id).count()
    if order_count > 0:
        return RedirectResponse(
            url=f"/products?error={quote('주문 내역이 있어 삭제할 수 없어요')}",
            status_code=303
        )

    db.delete(product)
    db.commit()
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
    current_user = get_current_user(request, db)
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
            "current_user": current_user,
            "error": error,
            "msg": msg,
            "active_page": "orders"
        }
    )

@app.post("/orders")
def create_order(
    request: Request,
    customer_id: int = Form(...),
    product_id: int = Form(...),
    quantity: int = Form(...),
    status: str = Form("완료"),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/orders?error={quote('관리자만 주문 생성 권한이 있습니다.')}",
            status_code=303
        )

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

    if quantity > product.stock:
        return RedirectResponse(
            url=f"/orders?error={quote('재고가 부족해요')}",
            status_code=303
        )

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
    request: Request,
    order_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/orders?error={quote('관리자만 상태 변경 권한이 있습니다.')}",
            status_code=303
        )

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

    if old_status != "취소" and new_status == "취소":
        if product:
            product.stock += order.quantity
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
def delete_order(
    request: Request,
    order_id: int,
    db: Session = Depends(get_db)
):
    from urllib.parse import quote
    current_user = get_current_user(request, db)
    if not current_user or current_user.role != "admin":
        return RedirectResponse(
            url=f"/orders?error={quote('관리자만 주문 삭제 권한이 있습니다.')}",
            status_code=303
        )

    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order:
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
