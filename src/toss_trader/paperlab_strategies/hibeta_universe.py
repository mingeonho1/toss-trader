"""hibeta_basket 후보 유니버스(동결) — **현재** S&P 500 대형·고베타 이름의 유한 목록.

포워드-온리라 생존편향이 없다(오늘의 멤버로 미래를 거래). 러너가 이 목록 ∩ 로컬 캐시로 좁혀
주입한다(캐시 없는 이름은 자동 제외). 목록은 의도적으로 유한(≈60)해 키리스 갱신을 유계로 유지한다.
캐시 편중(대형·고유동)으로 실현 베타는 목표(≈2)보다 낮게 나오며, 성과는 상한으로 읽는다.
"""
from __future__ import annotations

HIBETA_CANDIDATES = [
    # 반도체/AI(고베타 대표)
    "NVDA", "AMD", "AVGO", "MU", "MRVL", "SMCI", "ON", "LRCX", "KLAC", "AMAT",
    "QCOM", "NXPI", "MCHP", "TXN", "ADI",
    # 메가캡 성장
    "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA", "NFLX", "ORCL", "ADBE", "CRM",
    "NOW", "INTU", "AMD",
    # 소프트/클라우드 고베타
    "PLTR", "CRWD", "PANW", "SNOW", "DDOG", "NET", "FTNT", "WDAY", "TEAM", "APP",
    "ANET", "MPWR",
    # 크립토/핀테크/고변동 단일주
    "COIN", "MSTR", "PYPL", "SQ", "HOOD",
    # 소비/모빌리티/기타 고베타 대형
    "TSLA", "UBER", "ABNB", "CCL", "NCLH", "RCL", "DAL", "UAL", "CZR", "MGM",
    "ENPH", "FSLR", "ALB", "GM", "F", "DELL", "WDC", "STX", "AXON", "GEV",
]
# 중복 제거(가독성 위해 위 목록에 의도적 중복 존재).
HIBETA_CANDIDATES = list(dict.fromkeys(HIBETA_CANDIDATES))
