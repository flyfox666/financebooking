import calendar
from datetime import date, timedelta


def _due(year: int, month: int) -> date:
    return date(year, month, 15)


def filing_calendar(year: int) -> list[dict]:
    entries: list[dict] = []

    for month in range(1, 13):
        entries.append(
            {
                "due_date": _due(year, month).isoformat(),
                "tax": "个人所得税（工资薪金）",
                "period": f"{year}-{month:02d} 所属期",
                "description": "次月15日前在自然人电子税务局（扣缴端）申报，零税款也需全员全额零申报",
            }
        )

    for quarter in range(1, 5):
        due_month = quarter * 3 + 1
        due_year = year + (1 if due_month > 12 else 0)
        due_month = due_month if due_month <= 12 else due_month - 12
        period_months = f"{year}Q{quarter}"
        entries.append(
            {
                "due_date": date(due_year, due_month, 15).isoformat(),
                "tax": "增值税及附加税费（按季）",
                "period": period_months,
                "description": "季度终了15日内申报；先在系统导出申报辅助表核对",
            }
        )
        entries.append(
            {
                "due_date": date(due_year, due_month, 15).isoformat(),
                "tax": "企业所得税预缴（按季）",
                "period": period_months,
                "description": "季度终了15日内预缴；随附财务报表报送",
            }
        )
        entries.append(
            {
                "due_date": date(due_year, due_month, 15).isoformat(),
                "tax": "印花税（按季）",
                "period": period_months,
                "description": "买卖/技术/租赁合同按季汇总申报；营业账簿按年",
            }
        )

    books_due = date(year, 12, calendar.monthrange(year, 12)[1])
    entries.append(
        {
            "due_date": books_due.isoformat(),
            "tax": "印花税（营业账簿，按年）",
            "period": f"{year} 年度",
            "description": "年度终了15日内就实收资本+资本公积增加额申报",
        }
    )
    entries.append(
        {
            "due_date": date(year + 1, 5, 31).isoformat(),
            "tax": "企业所得税汇算清缴",
            "period": f"{year} 年度",
            "description": "次年5月31日前完成汇算清缴与纳税调整",
        }
    )
    entries.append(
        {
            "due_date": date(year + 1, 5, 31).isoformat(),
            "tax": "财务报表年报报送",
            "period": f"{year} 年度",
            "description": "含现金流量表（上海年报需报送）",
        }
    )

    entries.sort(key=lambda e: e["due_date"])
    return entries


def upcoming_reminders(within_days: int = 7, today: date | None = None) -> list[dict]:
    today = today or date.today()
    horizon = today + timedelta(days=within_days)
    result = []
    for year in (today.year, today.year + 1):
        for entry in filing_calendar(year):
            due = date.fromisoformat(entry["due_date"])
            if today <= due <= horizon:
                result.append({**entry, "days_left": (due - today).days})
    result.sort(key=lambda e: e["due_date"])
    return result
