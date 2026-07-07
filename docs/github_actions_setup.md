# GitHub Actions 자동화 설정 가이드

> **상태: 보류 (2026-07-08).** 워크플로 초안은 `docs/github-actions/`에 비활성 상태로 보관돼 있다.
> 활성화하려면 아래 0~3장을 먼저 준비한 뒤 파일들을 `.github/workflows/`로 옮겨 커밋하면 된다.
> self-hosted 러너와 시크릿 없이 옮기기만 하면 잡이 큐에 걸려 실패만 쌓이니 순서를 지킬 것.

워크플로 3개 초안 (`docs/github-actions/`):

| 워크플로 | 스케줄 (UTC) | 하는 일 | 실주문 |
|---|---|---|---|
| `dca.yml` | 평일 15:00 (정규장 중) | 적립 매수 `run_dca.py --auto --execute` | **있음 (live)** |
| `forward-paper.yml` | 평일 21:30 (마감 후) | 전략 비교 장부 갱신 + 리포트 커밋백 | 없음 |
| `collect-microstructure.yml` | 평일 13:30~20:30 매시 | 호가/체결 스냅샷 55분씩 수집 | 없음 |

## 0. 전제: 왜 `runs-on: self-hosted`인가 (IP 화이트리스트 문제)

**GitHub가 호스팅하는 무료 러너는 고정 IP가 불가능하다.** 러너는 매 실행마다 Azure의 수천 개 IP 대역 중 하나에서 뜨고, 대역 목록도 매주 바뀐다. 토스 API가 등록된 IP만 허용하므로, GitHub 호스팅 러너로는 API 호출이 전부 거부된다.

선택지 비교:

| 방법 | 비용 | 고정 IP | 비고 |
|---|---|---|---|
| **Self-hosted 러너 (권장)** | 무료 | 러너 머신의 IP | GitHub Actions UX(스케줄/로그/시크릿) 그대로, 실행만 내 머신 |
| GitHub larger runner + static IP | 유료 (Team/Enterprise 플랜) | 가능 | 개인 프로젝트엔 과함 |
| 호스팅 러너 + 고정IP 프록시 경유 | VPS 비용 | 프록시 IP | VPS가 있으면 그냥 거기서 돌리는 게 나음 |

Self-hosted 러너를 둘 곳 두 가지:

- **A. 지금 이 Mac** — 가장 간단. 단, Mac이 켜져 있어야 하고, 가정용 회선 IP는 공유기/모뎀 재부팅 시 바뀔 수 있다(바뀌면 토스 개발자센터에서 재등록). 사실상 launchd를 GitHub Actions UI로 바꾸는 것.
- **B. Oracle Cloud Always Free VM** — 평생 무료 티어, **진짜 고정 공인 IP**, 24시간 가동. Mac을 켜둘 필요가 없어지고 IP 재등록 이슈도 사라진다. 이게 정석.

## 1. Self-hosted 러너 등록 (5분)

GitHub 저장소 → **Settings → Actions → Runners → New self-hosted runner** → OS 선택(macOS 또는 Linux) → 화면에 나오는 명령 3줄을 러너 머신에서 실행:

```bash
# 예시 (실제 토큰은 화면에 표시되는 것 사용)
mkdir actions-runner && cd actions-runner
curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/...
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/<owner>/toss-trader --token <TOKEN>
./run.sh          # 포그라운드 테스트. 상시 가동은 아래 서비스 설치
```

상시 가동 서비스로 설치:

```bash
# macOS (launchd) / Linux (systemd) 공통
sudo ./svc.sh install && sudo ./svc.sh start
```

러너 머신 요구사항: `python3`만 있으면 됨 (이 레포는 외부 패키지 의존 없음).

## 2. Secrets 등록

저장소 → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret 이름 | 값 |
|---|---|
| `TOSS_CLIENT_ID` | 토스 개발자센터 API Key |
| `TOSS_CLIENT_SECRET` | 토스 개발자센터 Secret |
| `TOSS_ACCOUNT_SEQ` | `client.get_accounts()`로 조회한 계좌 시퀀스 |

선택 — **Variables** 탭:

| Variable | 기본값 | 용도 |
|---|---|---|
| `MICRO_SYMBOLS` | `QQQ,SPY` | 수집기 대상 심볼 |

`TRADING_MODE`는 시크릿이 아니라 워크플로에 하드코딩돼 있다: `dca.yml`만 `live`, 나머지는 `paper`. 실주문 경로를 워크플로 파일 리뷰만으로 감사할 수 있게 하기 위함.

## 3. 토스 개발자센터 IP 등록

러너 머신에서 공인 IP 확인 후 토스 개발자센터 허용 IP에 등록:

```bash
curl -s https://ifconfig.me
```

## 4. 보안 수칙 (중요)

1. **저장소는 반드시 private 유지.** self-hosted 러너가 붙은 public 저장소는 외부인이 PR로 러너에서 임의 코드를 실행할 수 있다(= API 키 탈취 경로). private이면 이 경로가 닫힌다.
2. 시크릿은 로그에 자동 마스킹되지만, 스크립트에서 토큰/키를 `print`하지 않는 원칙 유지.
3. `dca.yml`의 실주문은 3중 가드(정규장 체크, 세션당 1회 상태 가드, `TRADING_MODE=live`)에 더해 워크플로 `concurrency`로 동시 실행이 차단된다. 수동 실행(workflow_dispatch)은 기본이 dry-run이고, `execute` 체크박스를 켜야 실주문이다.
4. 세션당 1회 가드 상태는 러너 디스크의 `data/`에 있다(`checkout`이 `clean: false`라 보존됨). **러너 머신을 바꾸거나 워크스페이스를 지우면 가드가 리셋**되므로, 그날 이미 매수했는지 `data/dca.log`를 확인한 뒤 재실행할 것.

## 5. 데이터 보관

- `data/`는 gitignore — 페이퍼 장부 상태·수집 데이터·DCA 가드 전부 **러너 디스크에만** 쌓인다.
- 수집기는 하루에 심볼당 수 MB씩 쌓는다. 월 1회 정도 오래된 세션 디렉터리를 압축 권장:
  `find data/microstructure -maxdepth 1 -type d -mtime +30 -exec tar czf {}.tar.gz {} \; -exec rm -r {} \;`
- 러너를 Oracle VM으로 옮길 때는 `data/` 디렉터리를 같이 복사하면 장부·가드 연속성이 유지된다.

## 6. launchd와의 관계

기존 Mac launchd DCA 자동화(`scripts/install_dca_automation.sh`)와 `dca.yml`을 **동시에 켜두지 말 것**. 세션당 1회 가드가 같은 `data/` 상태를 보면 중복 매수는 막히지만, 러너 워크스페이스와 launchd 실행 경로가 다르면 가드 상태 파일도 달라져 이중 매수 위험이 있다. GitHub Actions로 전환하면 `launchctl unload`로 기존 것을 내릴 것.
