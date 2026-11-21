# Changelog

모든 주요 변경사항은 이 파일에 기록됩니다.
버전 규칙:
- **메이저 업데이트**: `0.1` 단위 증가, 마이너(패치) 버전은 `0`으로 초기화 (예: `0.1.2` -> `0.2.0`)
- **마이너 기능 업데이트 / 버그 픽스**: `0.0.1` 단위 증가 (예: `0.1.2` -> `0.1.3`)

---

## [0.2.4] - 2026-11-21

### 개선 및 수정 (Changed & Fixed)
- **ConfigFlow / OptionsFlow 드롭다운 한글화 (`config_flow.py`)**:
  - `vent_mode`(`ventilation_mode`), `vent_logic`(`ventilation_logic`), `tuning_mode`(`tuning_mode`), `prediction_mode`(`prediction_mode`), `inertia_profile`(`inertia_profile`), `sec0_action`(`sec0_action`) `SelectSelectorConfig`에 `translation_key` 적용
  - 구간 설정 단계(`async_step_sections_climate`)에 **구간 0 (`section_0`)** 대기/조기 종료 동작 및 셋백 보정값 UI 연결
- **진단 센서 상태값·이름 한글화 (`sensor.py`)**:
  - `_BaseSmartPIDDiagnosticSensor`의 하드코딩된 `_attr_name` 제거로 `entity.sensor.<key>.name` 번역 적용
  - `SmartPIDCyclePhaseSensor` 및 `SmartPIDVentilationStateSensor`에 `SensorDeviceClass.ENUM` 및 `_attr_options` 지정
- **번역 리소스 일괄 동기화 (`strings.json`, `translations/ko.json`, `translations/en.json`)**:
  - 최상위 `selector` 블록(6종 드롭다운), `section_0`, `data_description`, 진단 센서 상태값·속성 번역 추가 및 `strings.json` 내 영문 잔재 한국어 통일
- **문서 및 회귀 테스트 보강 (`README.md`, `tests/`)**:
  - `README.md`에 전체 동작 구조, 자가학습 원리(4단계 코스팅·Huber-RLS·SIMC), 학습 이후 동작 변화, 5단계 실전 사용 가이드 및 진단 센서 해석표 정리
  - 셀렉터 `translation_key`·번역 누락 및 진단 센서 `ENUM` 매핑 회귀 테스트 추가

---

## [0.2.3] - 2026-11-21

### 수정 (Fixed)
- Home Assistant 2027.11 호환성을 위해 `SmartPIDThermostatEntity` (`climate.py`)의 deprecated 온도 속성 마이그레이션:
  - `_attr_temperature_unit` → `_attr_native_temperature_unit`
  - `current_temperature` → `native_current_temperature`
  - `target_temperature` → `native_target_temperature`
- `SmartPIDThermostatEntity` `native_*` 속성 사용 여부 검증 회귀 테스트 추가

---

## [0.2.2] - 2026-11-20

### 수정 (Fixed)
- `config_flow.py` 내 환기/자가학습 섹션(`CONF_VENT_MODE` 등) 상수 임포트 누락으로 초기 설정 화면 진입 시 발생하던 `NameError` 수정
- ConfigFlow / OptionsFlow 폼 렌더링 회귀 테스트 추가

---

## [0.2.1] - 2026-11-20

### 수정 (Fixed)
- HACS 업데이트 시 `hacs.json` 누락으로 실패하던 문제를 해결하기 위해 저장소 루트에 `hacs.json` 추가

---

## [0.2.0] - 2026-11-20

### 추가 (Added)
- **4상태 환기 감지기 (`ventilation.py`)**: `disabled` / `sensors`(`all`/`any`) / `switch` / `combined` 모드, 45초 디바운스, 20분 쿨다운, 5분 센서 불능 유예(`degraded` 학습 차단) 지원
- **Section 0 대기/조기 종료**: `climate_delta` 저출력·환기 시 `setback` / `fan_only` / `off` 동작, 최소 유지시간(`300s`/`240s`) 및 적분 동결 적용
- **순수 Python 자가학습 엔진 (`psychrometrics.py`, `learner.py`, `storage.py`)**:
  - Bolton(1980) 습공기 변환(`rh_to_ah`, `ah_to_rh`, `dew_point`)
  - Huber-RLS 온라인 모델러(망각 인자 `0.992`, `hard_reject=0.12`, `huber_delta=0.04`)
  - `fast_air` / `high_inertia` 동적 코스팅 학습기 및 Skogestad SIMC 자동 튜너
  - 5사이클 사후 검증(30% 초과 성능 악화 시 스냅샷 자동 롤백 및 HA 영구 알림)
  - `ModelStore` 10분 디바운싱(`async_delay_save`) 영구 저장
- **런타임 분리 & 진단 센서 & 서비스 (`runtime.py`, `sensor.py`, `services.yaml`)**:
  - `entry.runtime_data` 기반 `SmartPIDRuntime`, `shadow_learn` 관찰 모드, `EntityCategory.DIAGNOSTIC` 센서 5종, `smart_pid_airconditioner.reset_learning` 서비스
- **안전/교차 연동/부트스트랩**: 외부 Context 수동 조작 30분 유예, 연속 가동 120분 워치독/15분 강제 휴지, 제습기 발열 `u_aux_heat`·에어컨 냉방 인터록 교차 연동, 듀얼 모드 최소 데드밴드(`2.0°C` / `5.0% RH`), 선택적 Recorder 이력 부트스트랩
- **단위·통합 테스트 (`tests/`)**: `pytest` 기반 회귀·물리 시뮬레이션·통합 테스트 스위트 구축 및 `translations/en.json` 추가

### 개선 (Changed)
- **PID 수치 안정화**: D항 1차 LPF($\tau_d = 120\text{s}$) 적용 및 `<10s` 센서 이벤트 D항 스파이크 차단, I항 분 단위 정규화(`dt / 60.0`) 및 v1→v2 ConfigEntry 마이그레이션 추가
- **HA 2025.12–2026 표준화**: `OptionsFlowWithReload` 전환(중복 `add_update_listener` 제거) 및 `ExtraStoredData` 기반 보호 타이머·섹션·적분 영속 복원

### 수정 (Fixed)
- `climate.py` `async_set_temperature()` 내 `ATTR_HUMIDITY` 미임포트 `NameError` 버그 수정
- 습도 유형 엔티티에서 `_indoor_temp`/`_outdoor_temp`에 습도 값을 덮어쓰지 않도록 온도/습도 변수 분리

---

## [0.1.2] - 2026-10-19

### 추가 (Added)
- Ki 적분 제한 범위(`max_i`) 설정 파라미터 추가
- 양방향 적분 클램핑(`[-max_i, +max_i]`) 및 포화 시 적분 누적 중단(Anti-windup) 로직 개선

---

## [0.0.2] - 2026-10-18

### 개선 (Changed)
- 가습기/제습기 명령 디스패치 순서 최적화
- Derivative Kick(설정값 변경 시 급격한 미분 충격) 방지를 위해 측정값 기반 미분(Derivative on Measurement) 적용
- 섹션 전환 및 다중 명령 시 통신 안정성을 위한 딜레이 및 디스패치 보호 가드 추가

---

## [0.0.1] - 2026-10-18

### 초기 릴리즈 (Initial Release)
- Home Assistant용 Smart PID Air Conditioner 커스텀 컴포넌트 첫 배포
- Climate(에어컨/히터) 및 Humidifier(가습기/제습기) 지원
- 스마트 PID + 건물 외기 피드포워드($K_e$) 결합 제어
- 4단계 섹션 분할(Section Mapping) 및 $\pm 1.5\%$ 히스테리시스 밴드 제어
- Inverter Climate Delta 제어 및 Switch PWM 시간비례 제어 모드 지원
- Home Assistant Config Flow 및 Options Flow UI 지원
