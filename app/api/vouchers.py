from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.core.db import get_db
from app.core.auth import get_current_user
from app.models.system import User
from app.core import voucher_service
from app.core import report_service

router = APIRouter(prefix="/api/vouchers", tags=["凭证管理"])


class VoucherEntryCreate(BaseModel):
    account_id: int
    summary: str = ''
    debit: float = 0
    credit: float = 0
    aux_type: Optional[str] = None
    aux_id: Optional[int] = None
    aux_name: Optional[str] = None


class VoucherCreate(BaseModel):
    period: str = ''
    voucher_date: Optional[str] = None
    summary: str = ''
    is_adjusting: int = 0
    entries: List[VoucherEntryCreate]


class VoucherResponse(BaseModel):
    id: int
    period: str
    voucher_no: str
    voucher_date: datetime
    summary: str
    status: str
    total_amount: float


@router.post("", response_model=VoucherResponse)
def create_voucher(data: VoucherCreate, db=Depends(get_db), user: User = Depends(get_current_user)):
    """创建凭证"""
    try:
        voucher = voucher_service.create_voucher(db, data.dict(), user.id)
        
        # 计算金额
        total = sum(float(e.debit) for e in voucher.entries)
        
        return VoucherResponse(
            id=voucher.id,
            period=voucher.period,
            voucher_no=voucher.voucher_no,
            voucher_date=voucher.voucher_date,
            summary=voucher.summary,
            status=voucher.status,
            total_amount=round(total, 2)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def list_vouchers(
    db=Depends(get_db),
    user: User = Depends(get_current_user),
    period: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
):
    """获取凭证列表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    filters = {
        'period': period,
        'status': status,
        'voucher_no': keyword,
        'page': page,
        'page_size': page_size
    }
    
    items, total = voucher_service.get_voucher_list(db, filters)
    
    result = []
    for v in items:
        total = sum(float(e.debit) for e in v.entries)
        result.append({
            'id': v.id,
            'period': v.period,
            'voucher_no': v.voucher_no,
            'voucher_date': v.voucher_date.isoformat(),
            'summary': v.summary,
            'status': v.status,
            'total_amount': round(total, 2),
            'entry_count': len(v.entries)
        })
    
    return {
        'items': result,
        'total': total,
        'page': page,
        'page_size': page_size
    }


@router.get("/{voucher_id}")
def get_voucher(voucher_id: int, db=Depends(get_db), user: User = Depends(get_current_user)):
    """获取凭证详情"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    voucher = voucher_service.get_voucher_detail(db, voucher_id)
    if not voucher:
        raise HTTPException(status_code=404, detail="凭证不存在")
    
    total = sum(float(e.debit) for e in voucher.entries)
    
    return {
        'id': voucher.id,
        'period': voucher.period,
        'voucher_no': voucher.voucher_no,
        'voucher_date': voucher.voucher_date.isoformat(),
        'summary': voucher.summary,
        'status': voucher.status,
        'total_amount': round(total, 2),
        'is_adjusting': voucher.is_adjusting,
        'entries': [
            {
                'id': e.id,
                'account_id': e.account_id,
                'account_code': e.account_code,
                'account_name': e.account_name,
                'summary': e.summary,
                'debit': float(e.debit),
                'credit': float(e.credit),
                'aux_type': e.aux_type,
                'aux_id': e.aux_id,
                'aux_name': e.aux_name
            }
            for e in voucher.entries
        ]
    }


@router.post("/{voucher_id}/post")
def post_voucher(voucher_id: int, db=Depends(get_db), user: User = Depends(get_current_user)):
    """审核/过账凭证"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    try:
        voucher = voucher_service.post_voucher(db, voucher_id)
        return {'message': f'凭证 {voucher.voucher_no} 已过账'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{voucher_id}/reverse")
def reverse_voucher(voucher_id: int, reason: str = '', db=Depends(get_db), user: User = Depends(get_current_user)):
    """红冲凭证"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    try:
        reverse_v = voucher_service.reverse_voucher(db, voucher_id, reason)
        return {'message': f'已创建红冲凭证 {reverse_v.voucher_no}'}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============ 报表接口 ============

@router.get("/reports/profit")
def get_profit_statement(
    period: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取利润表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    return report_service.generate_profit_statement(db, period)


@router.get("/reports/trial-balance")
def get_trial_balance(
    period: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取试算平衡表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    return report_service.generate_trial_balance(db, period)


@router.get("/reports/balance-sheet")
def get_balance_sheet(
    period: str,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """获取资产负债表"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    return report_service.generate_balance_sheet(db, period)


# ============ 自动制单接口 ============

@router.post("/auto-from-doc/{doc_id}")
def auto_create_voucher(
    doc_id: int,
    db=Depends(get_db),
    user: User = Depends(get_current_user)
):
    """根据业务单据自动生成凭证"""
    if user.role.code not in ['ADMIN', 'GM', 'FINANCE']:
        raise HTTPException(status_code=403, detail="权限不足")
    
    try:
        voucher = voucher_service.auto_create_voucher_from_doc(db, doc_id)
        if voucher:
            return {
                'message': f'已自动生成凭证 {voucher.voucher_no}',
                'voucher_id': voucher.id
            }
        else:
            return {'message': '该单据无需自动生成凭证或生成失败'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
