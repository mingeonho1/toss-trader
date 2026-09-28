"""페이퍼 랩 전략이 쓰는 심볼 유니버스 상수(동결).

- ``NDX_100``: mom_top5_ndx 용 현재(2026) 나스닥100 근사 목록. 포워드-온리라 생존편향 무관.
  캐시에 없는 이름은 런타임에 교집합으로 걸러진다.
- ``ETF_LIKE``: hot_rvol_swing 이 '단일종목'만 고르도록 제외할 ETF/레버리지/섹터 심볼.
- ``DEFAULT_SWING_UNIVERSE``: 캐시 유니버스 주입이 없을 때의 폴백(대형 무버 워치리스트).
"""
from __future__ import annotations

NDX_100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "AVGO", "TSLA", "COST",
    "NFLX", "ADBE", "PEP", "AMD", "CSCO", "TMUS", "INTC", "CMCSA", "QCOM", "TXN",
    "AMGN", "INTU", "HON", "AMAT", "BKNG", "ISRG", "VRTX", "ADP", "GILD", "ADI",
    "REGN", "MU", "LRCX", "MDLZ", "PANW", "SNPS", "KLAC", "CDNS", "MELI", "PYPL",
    "MAR", "ABNB", "ORLY", "CTAS", "CSX", "WDAY", "MRVL", "FTNT", "NXPI", "ADSK",
    "PCAR", "ROP", "CPRT", "MNST", "PAYX", "AEP", "KDP", "ODFL", "CHTR", "ROST",
    "FANG", "FAST", "DDOG", "EA", "KHC", "VRSK", "EXC", "CTSH", "GEHC", "CCEP",
    "XEL", "LULU", "IDXX", "TTD", "BKR", "ON", "CSGP", "DXCM", "ZS", "ANSS",
    "TEAM", "WBD", "BIIB", "ILMN", "MDB", "CRWD", "PDD", "LIN", "DLTR", "ENPH",
    "APP", "ARM", "SMCI", "DASH", "CEG", "MRNA", "GFS", "WBA", "SIRI", "DOCU",
]

ETF_LIKE = {
    "QQQ", "TQQQ", "SQQQ", "QLD", "SPY", "SSO", "UPRO", "SPXU", "GLD", "SLV", "DBC",
    "IEF", "TLT", "SHY", "BIL", "EEM", "EFA", "VEA", "VTI", "VNQ", "IWM", "DIA",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "SCHD", "SOXL", "SOXS", "SOXX", "SMH", "TMF", "UVXY", "SVXY", "VXX", "FNGU",
    "ARKK", "BITO", "GBTC", "IBIT", "SHV", "AGG", "BND", "LQD", "HYG", "USO",
}

DEFAULT_SWING_UNIVERSE = [
    "NVDA", "TSLA", "AMD", "META", "MSFT", "AAPL", "AMZN", "GOOGL", "NFLX", "AVGO",
    "PLTR", "MSTR", "SMCI", "COIN", "SOFI", "RIVN", "LCID", "SHOP", "UBER", "SNOW",
    "NET", "DDOG", "CRWD", "MELI", "ARM", "APP", "DELL", "MU", "MRVL", "ON",
    "HOOD", "RBLX", "DKNG", "CELH", "FSLR", "ORCL", "NOW", "PDD", "ANET", "AXON",
]
