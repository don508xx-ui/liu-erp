from pydantic import BaseModel
from typing import Optional, Any, List
from datetime import datetime


class Resp(BaseModel):
    code: int = 0
    msg: str = "ok"
    data: Optional[Any] = None

    @classmethod
    def ok(cls, data=None, msg="ok"):
        return cls(code=0, msg=msg, data=data)

    @classmethod
    def fail(cls, msg: str, code: int = 1):
        return cls(code=code, msg=msg, data=None)


class PageResp(BaseModel):
    code: int = 0
    msg: str = "ok"
    total: int = 0
    data: List[Any] = []
