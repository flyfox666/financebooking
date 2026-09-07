"""申报所属期日历；全国统一期限与待核对的预计日期分别标识。"""
from datetime import date, timedelta

SOURCE_2026 = "https://fgk.chinatax.gov.cn/zcfgk/c102424/c5245729/content.html"
DEADLINES = {2026: (20, 24, 16, 20, 22, 15, 15, 17, 15, 26, 16, 15)}


def _due(year: int, month: int) -> date:
    days = DEADLINES.get(year)
    return date(year, month, days[month - 1] if days else 15)


def filing_calendar(year: int, *, vat_frequency: str = "quarterly", entity_type: str = "company") -> list[dict]:
    """year 指税款所属年度。未载入官方年度表时不能声称已确认截止日。"""
    if not 2000 <= year <= 2098 or vat_frequency not in ("monthly", "quarterly"):
        raise ValueError("年度或增值税申报频率无效")
    entries = []

    def add(tax, period, due, description, *, national=True):
        verified = national and due.year in DEADLINES
        entries.append({
            "due_date": due.isoformat(), "tax": tax, "period": period,
            "description": description,
            "deadline_status": "official" if verified else "unverified",
            "deadline_note": "全国统一期限；如有主管税务机关特别调整，以其通知为准" if verified else "预计日期，须核对主管税务机关通知",
            "source_url": SOURCE_2026 if verified else None,
        })

    for month in range(1, 13):
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        due = _due(next_year, next_month)
        period = f"{year}-{month:02d}"
        add("个人所得税（工资薪金，如适用）", period, due, "本条为上月工资薪金扣缴所属期；请按实际扣缴义务核对")
        if vat_frequency == "monthly":
            add("增值税及附加税费（按月）", period, due, "按月视图；请与税务登记的实际申报频率核对")

    for quarter in range(1, 5):
        due_year, due_month = (year + 1, 1) if quarter == 4 else (year, quarter * 3 + 1)
        due, period = _due(due_year, due_month), f"{year}Q{quarter}"
        if vat_frequency == "quarterly":
            add("增值税及附加税费（按季）", period, due, "按季视图；请与税务登记的实际申报频率核对")
        if entity_type == "company":
            add("企业所得税预缴（按季，如适用）", period, due, "适用于按季预缴的企业，申报前核对利润及调整项目")
        # 印花税期限依应税凭证与主管税务机关确定，不把所有账套设为按季。

    if entity_type == "company":
        add("企业所得税汇算清缴", f"{year}年度", date(year + 1, 5, 31),
            "通常在年度终了后五个月内；具体截止日须核对当年通知", national=False)
    entries.sort(key=lambda entry: (entry["due_date"], entry["tax"]))
    return entries


def upcoming_reminders(within_days: int = 7, today: date | None = None, *,
                       vat_frequency: str = "quarterly", entity_type: str = "company") -> list[dict]:
    today = today or date.today()
    horizon = today + timedelta(days=within_days)
    result = []
    # 上年四季度、十二月及汇算事项会在本年到期，不能漏掉。
    for year in range(today.year - 1, horizon.year + 1):
        for entry in filing_calendar(year, vat_frequency=vat_frequency, entity_type=entity_type):
            due = date.fromisoformat(entry["due_date"])
            if today <= due <= horizon:
                result.append({**entry, "days_left": (due - today).days})
    return sorted(result, key=lambda entry: (entry["due_date"], entry["tax"]))
