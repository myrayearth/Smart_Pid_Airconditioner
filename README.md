# Smart PID Air Conditioner & Thermostat (`smart_pid_airconditioner`)

Home Assistant용 지능형 냉방, 난방, 가습, 제습 가상 통합구성요소(Custom Component)입니다.

실내 및 실외 센서(온도 / 습도)를 실시간 분석하여 냉방, 난방, 가습, 제습 모드에 최적화된 스마트 PID 알고리즘 및 외기 피드포워드 계수($K_e$)를 결합한 출력값(0~100%)을 계산합니다. 계산된 출력을 4개 섹션(Section)으로 나누어 하위 기기(에어컨, 히터, 가습기, 제습기, PWM 스위치)의 설정값(Delta 오프셋), 팬 속도, 운전 모드를 정밀하고 안정적으로 자동 제어합니다.

---

## 🌟 주요 기능 및 특징

### 1. 다양한 제어 기기 및 모드 지원
- **에어컨 / 히터 (Climate)**:
  - 냉방(`COOL`), 난방(`HEAT`), 자동(`AUTO`), 송풍(`FAN_ONLY`), 제습(`DRY`) 모드 지원.
  - 건물 외기 온도 피드포워드($K_e$) 반영.
- **가습기 / 제습기 (Humidifier)**:
  - 가습(`HUMIDIFIER`), 제습(`DEHUMIDIFIER`) 모드 지원.
  - 타겟 가습기/제습기가 지원하는 `mode` 목록을 자동 조회하여 각 섹션별 운전 모드 선택 가능.
  - 외기 습도 피드포워드($K_e$) 보상 지원 (실외 센서 미설정 시 $K_e$ 자동 무시/비활성화).
- **스위치 PWM 모드**:
  - 인버터 방식 외에도 일반 ON/OFF 스위치 및 릴레이 기기를 PWM 방식으로 시간 비례 제어 가능.

### 2. 하위 기기 지원 모드 자동 감지
- 제어할 대상 기기(`climate` 또는 `humidifier`)를 선택하면 기기가 지원하는 실제 동작 모드(`hvac_modes`, `modes`)와 팬 모드(`fan_modes`)를 자동으로 조회하여 설정 화면에 구성합니다.
- 기기에서 지원하지 않는 모드가 잘못 선택되는 문제를 원천 방지합니다.

### 3. 섹션(Section)별 세부 제어 및 액션(Action) 지정
- **PID 출력 4개 섹션**:
  - 섹션 1 (0% ~ 30%)
  - 섹션 2 (30% ~ 60%)
  - 섹션 3 (60% ~ 90%)
  - 섹션 4 (90% ~ 100%)
- **섹션별 제어 항목 On/Off**:
  - 설정값(Delta) 적용 여부, 팬 모드 적용 여부, 운전모드 적용 여부, 전원 액션(`on` / `off`) 적용 여부를 각 섹션별로 개별 설정 가능.
  - 가습기/제습기의 경우 특정 섹션에서 기기 전원을 끄거나(`off`) 켤 수 있도록 섹션별 전원 제어 지원.

### 4. 명령 안정성 및 디바이스 보호 딜레이
- **섹션 전환 시 딜레이**: 섹션이 변경될 때 첫 명령 전송 전 **1.0초** 대기하여 기기가 안정적으로 상태를 준비할 수 있도록 보장.
- **다중 명령 간 딜레이**: 한 제어 주기 내에서 여러 개의 명령(전원, 목표값, 운전모드, 팬속도 등)을 보낼 때 명령 사이에 **0.5초** 간격을 두어 IoT 기기(Zigbee, Wi-Fi, IR 리모컨 등)의 명령 유실 방지.

### 5. 외기 센서 유연성
- 실외 온도/습도 센서가 없는 환경에서도 오류 없이 사용 가능 (`Ke` 외기 피드포워드 항 자동 0.0 처리).

---

## 📁 디렉토리 구조

```
custom_components/smart_pid_airconditioner/
├── __init__.py           # 통합구성요소 셋업 및 옵션 업데이트 리스너
├── climate.py            # ClimateEntity 상속 (에어컨/히터 PID 제어 및 디스패처)
├── humidifier.py         # HumidifierEntity 상속 (가습기/제습기 PID 제어 및 디스패처)
├── config_flow.py        # UI 설정 및 옵션 플로우 마법사 (자동 기기 탐색 및 지원 모드 매핑)
├── const.py              # 상수, 기본값, 도메인 정의
├── manifest.json         # 통합구성요소 메타데이터
├── strings.json          # 기본 UI 텍스트
└── translations/
    └── ko.json           # 한국어 번역 리소스
```

---

## 🚀 설치 방법

### 수동 설치
1. `custom_components/smart_pid_airconditioner` 폴더를 Home Assistant의 `config/custom_components/` 디렉토리에 복사합니다.
2. Home Assistant를 재시작합니다.
3. **설정 > 기기 및 서비스 > 통합구성요소 추가**에서 **Smart PID Air Conditioner**를 검색하여 추가합니다.

### HACS 설치 (사용자 정의 저장소)
1. HACS > 통합구성요소 > 우측 상단 메뉴 > **사용자 정의 저장소 추가**
2. 저장소 URL: `https://github.com/myrayearth/Smart_Pid_Airconditioner`
3. 범주: **통합구성요소 (Integration)**
4. 다운로드 후 Home Assistant를 재시작합니다.
