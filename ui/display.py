"""DB의 UTC 시각을 화면용 한국 시간으로 변환한다. 저장 값은 바꾸지 않는다."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def format_kst(value):
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "-"
    if not isinstance(value, datetime):
        return "-"
    # 과거의 timezone 없는 DB 시각도 UTC로 해석한다.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
