---
name: smart-pid-airconditioner
version: "1.1.0"
project_name: "Smart PID Air Conditioner"
default_tier: 2          # 1: 즉시 실행 / 2: 요약 후 실행 / 3: Plan 승인 필수
strict_test: true        # true일 경우 테스트/검증 결과 보고 필수
domain_type: "iot"       # web | iot | media_mcp | api_trading | general
git_enabled: true
github_repo: "https://github.com/myrayearth/smart_pid_airconditioner.git"
---

# Project Rules: Home Assistant & ESPHome IoT
- **Project Tier Default**: Tier 2 (표준 기능 변경은 설계 요약 후 구현·검증, 하드웨어·전원·안전 제어 등 고위험 변경은 Tier 3 상향 적용)

## 🔄 [최우선 시작 규칙] GitHub 버전 사전 검사 & Push/Pull 자동 판정 (Pre-Flight Sync)
> *(GitHub 원격 저장소 연동 프로젝트인 경우 적용)*
> 새로운 대화 세션을 시작하거나 코딩 작업에 착수하기 전(단순 질의 제외), 에이전트는 **반드시 다음 절차를 먼저 실행**합니다:
> 1. **상태 진단**: `git fetch origin main` & `git status -uno`로 로컬과 원격(`origin/main`) 커밋/변경 상태를 대조합니다.
> 2. **버전 판정 및 분기 처리 (`ask_question`)**:
>    - **[Behind (원격 최신)]**: `"🌐 GitHub(원격 저장소)이 로컬보다 최신입니다. 최신 내용을 로컬로 가져올까요(Pull)?"`
>    - **[Ahead / 로컬 변경]**: `"💻 내 컴퓨터(로컬)의 작업 내용이 GitHub보다 최신입니다. GitHub에 업로드할까요(Push)?"`
>    - **[Diverged (양쪽 수정)]**: `"⚠️ 로컬과 GitHub 양쪽에 서로 다른 변경사항이 있습니다. 어떻게 동기화할까요?"`
>    - **[Up-to-date (일치)]**: 추가 질문 없이 즉시 본 작업을 진행합니다.

## 🛠️ 기술 스택 및 환경
- **Host Environment**: Debian 12, Home Assistant Supervised, Zigbee2MQTT
- **Target**: Home Assistant Custom Component (`custom_components/`), ESPHome (C++ Custom Header / YAML)

## 📌 프로젝트 필수 규칙
1. **비동기 논블로킹(Non-blocking) 원칙**: Home Assistant 코어 이벤트 루프를 블로킹하는 동기 I/O 호출을 엄격히 금지하며, 모든 상태 업데이트와 통신은 `async/await` 기반으로 작성한다.
2. **엔티티 및 식별자 규칙**: 자동화나 컴포넌트 연동 시 `device_id` 대신 명확한 `entity_id`를 기준으로 참조한다.
3. **안전 장치(Fail-safe) 필수 구현**:
   - PID 제어, PWM 스위칭, 공조기/릴레이 제어 시 센서 값 결측(`unavailable`/`unknown`)이나 통신 두절에 대비한 안전 정지(Fallback) 로직을 반드시 포함한다.
   - 릴레이나 압축기의 물리적 손상을 막기 위해 최소 작동/휴지 시간(Min Cycle Duration) 제약을 코드 수준에서 강제한다.
4. **다국어 및 UI 설정**: Config Flow 구현 시 한국어 번역 파일(`translations/ko.json`)을 항상 동기화해 작성한다.
5. **작업 완료 시 자동 버전업 (Auto Version Bump)**: 컴포넌트 기능 구현·수정 및 테스트 검증이 완료되면 별도 요청 없이도 `CHANGELOG.md` 버전 규칙(메이저 기능 업데이트 +0.1, 마이너/버그 픽스 +0.0.1)에 따라 `manifest.json`과 `CHANGELOG.md`를 버전업하고 Git 태그(`vX.Y.Z`)를 생성한다. (규칙/문서 전용 변경은 버전업 제외)
6. **대기열 기반 Git 커밋 · GitHub 푸시 · HA 커스텀 컴포넌트 업데이트**:
   - **모든 요청에 `git commit` 진행**: 파일 변경이 발생한 모든 요청 완료 시 반드시 로컬 `git commit`을 수행한다.
   - **대기열 확인 (`t3_queue(action="list")`) 후 분기**:
     - **대기열에 다음 요청이 등록되어 있는 경우**: `git commit`까지만 진행하고(GitHub 푸시 및 HA 업데이트 보류), 즉시 다음 요청을 수행한다.
     - **대기열에 등록된 다음 요청이 없는 경우**:
       1. 로컬 커밋(및 생성된 `vX.Y.Z` 태그)을 원격 저장소(`origin main` 및 태그)로 **GitHub 푸시**한다.
       2. Home Assistant MCP(`ha_manage_hacs`: `action="update_information"` → `action="download"`, `repository_id="myrayearth/Smart_Pid_Airconditioner"`)를 호출해 **HA 커스텀 컴포넌트 업데이트**를 진행한다. (최신 GitHub 릴리스가 이미 HA에 설치되어 있으면 생략한다.)
