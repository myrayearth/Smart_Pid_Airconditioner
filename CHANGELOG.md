# Changelog

모든 주요 변경사항은 이 파일에 기록됩니다.
버전 규칙:
- **메이저 업데이트**: `0.1` 단위 증가, 마이너(패치) 버전은 `0`으로 초기화 (예: `0.1.2` -> `0.2.0`)
- **마이너 기능 업데이트 / 버그 픽스**: `0.0.1` 단위 증가 (예: `0.1.2` -> `0.1.3`)

---

## [0.4.1] - 2026-11-24

### 수정 (Fixed)
- **`bootstrap_from_influxdb` 실행 시 `DurationSelector` dict `TypeError` 수정 (`runtime.py`)**:
  - OptionsFlow에서 `ventilation.vent_debounce_sec`가 `{"hours": 0, "minutes": 0, "seconds": 45}` 딕셔너리 형태로 저장된 엔트리에서 `int()` 변환 시 발생하던 `TypeError`를 `duration_to_seconds()` 변환 및 방어적 타입 파싱으로 해결
- **InfluxDB 문자열 수치 필드 및 서비스 UI 공란/리스트 입력 방어 (`influxdb_bootstrap.py`, `__init__.py`)**:
  - 수치형 센서가 문자열 필드(`"state"`)에 저장된 경우에도 `MEAN()` 쿼리 오류 없이 1분 평균 fallback 집계를 지원하도록 보강
  - 서비스 스키마에서 `entity_id` 리스트 전달 및 선택 숫자 필드의 공란(`""`)/실수(`float`) 입력 허용
- **회귀 테스트 추가 (`tests/test_influxdb_bootstrap.py`)**

---

## [0.4.0] - 2026-11-23

### 추가 (Added)
- **InfluxDB v1 장기 과거 이력 자가학습 초기화 서비스 (`influxdb_bootstrap.py`, `__init__.py`, `services.yaml`)**:
  - 신규 서비스 `smart_pid_airconditioner.bootstrap_from_influxdb` (`SupportsResponse.OPTIONAL`) 등록
  - 외부 PyPI 패키지 없이 HA 내장 `async_get_clientsession`과 Python 표준 라이브러리만으로 InfluxDB v1 HTTP `GET /query` (`epoch=ms`, `BasicAuth` 비밀번호 비노출) 통신 구현
  - 최대 **3년(1,095일)** 데이터를 **30일 단위 청크**로 순차 조회하고 각 청크 시작 전 `ORDER BY time DESC LIMIT 1` LOCF 시드 적용
  - HA InfluxDB 태그 관례(도메인 제거 짧은 ID·전체 ID) 및 유형별 measurement(`°C`/`°F`→°C 자동 환산/`state`/`sensor`, `%`/`state`, `state`/`switch`/`input_boolean` 등) 자동 탐색, 숫자형 `1`/`0` 및 문자열 가동 상태 지원
  - **계절·구간 분리 필터링(Regime Segmentation)**: `min_indoor_temp` / `max_indoor_temp`, `min_indoor_humidity` / `max_indoor_humidity` 범위 이탈 또는 30분 초과 결측 시 진행 중 `DynamicCoastingLearner` 사이클(`abort_cycle_to_idle`)과 5분 RLS 누적 블록을 즉시 단절하고, 이미 수렴된 모델 파라미터·공분산 행렬·코스팅 계수는 보존해 다년간 동일 계절 데이터 누적 학습 지원
  - 습도 제어 시 `rh_to_ah()` 절대습도 변환 재생, `VentilationGuard` 기반 환기 및 닫힘 후 20분 쿨다운 차단, `dry_run` 미리보기 모드 지원
- **코어 런타임·학습기 확장 (`const.py`, `learner.py`, `runtime.py`)**:
  - `DynamicCoastingLearner.abort_cycle_to_idle()`, `SmartPIDRuntime.get_bootstrap_defaults()`, `SmartPIDRuntime.async_apply_bootstrapped_snapshot()` 추가 및 `entity_registry` 기반 정확한 `entity_id`→`runtime` 매핑 적용
- **서비스 UI 메타데이터·번역 및 회귀 테스트 추가 (`strings.json`, `translations/*.json`, `tests/conftest.py`, `tests/test_influxdb_bootstrap.py`)**:
  - `services.yaml` 22개 입력 필드 및 한/영 번역 동기화, Mock InfluxQL 기반 2년 계절 필터·사이클 단절·95일 4청크·습도/환기·`dry_run`·인증/오류 처리 테스트 7종 추가

---

## [0.3.0] - 2026-11-22

### 추가 (Added)
- **사용자 지정 열 관성 프로파일(`custom`) 및 시간 직접 설정 (`const.py`, `learner.py`, `storage.py`, `runtime.py`, `config_flow.py`)**:
  - `fast_air`(관찰 45분 / τ 10분)·`high_inertia`(관찰 180분 / τ 35분) 외에 **`사용자 지정(custom)`** 프로파일 추가
  - UI에서 **사용자 지정 코스팅 관찰 최대 창(`custom_coast_window_min`, 15~720분, 기본 90분)** 및 **열 시상수 τ(`custom_tau_em_min`, 5~180분, 기본 25분)**를 직접 지정·저장 가능
- **UI 설정 엔티티 3종 및 HA 진단 정보 다운로드 (`button.py`, `select.py`, `diagnostics.py`)**:
  - `SmartPIDResetLearningButton` (`EntityCategory.CONFIG`): 기기 화면 버튼으로 자가학습 모델·코스팅 이력·오차 통계 즉시 초기화
  - `SmartPIDTuningModeSelect` & `SmartPIDPredictionModeSelect` (`EntityCategory.CONFIG`): 전체 통합구성요소 리로드 없이 `튜닝 모드`·`예측 제어 성향(SIMC)` 실시간 전환
  - `async_get_config_entry_diagnostics`: 기기 화면에서 구조화된 학습 스냅샷 및 예측 오차 JSON 다운로드 지원
- **주간 일사량 가드(`solar_guard`) (`const.py`, `config_flow.py`, `learner.py`, `runtime.py`)**:
  - 난방 제어 시 `sun.sun`이 지평선 위에 있을 때 햇빛 유입으로 발생하는 급격한 온도 상승을 기기 잔열 코스팅으로 오학습하지 않도록 보호 (`solar_gain_contamination` 기각)
- **스위치 명령 가드(`SwitchCommandGuard`) (`actuator.py`, `climate.py`, `humidifier.py`)**:
  - `OFF` 명령의 `ON` 선점 취소(generation 기반), `OFF` 상태 확인 대기(`1.5초`) 및 제한적 재시도(`0.3초`·`0.7초`), `ON` 실패 시 안전 `OFF` 복구 적용

### 개선 (Changed)
- **2단계 연속 반전 코스팅 정점 확정 및 이상치 격리 (`learner.py`)**:
  - `peak_confirm_delta` 기반 2회 연속 반전 확인으로 센서 노이즈로 인한 조기 정점 판정 방지
  - 중앙값 대비 극단적 코스팅 이상치는 1회만 발생 시 `inconsistent_coast_outlier`로 격리 보류하고 2회 이상 유사 패턴 관측 시에만 반영
- **실측 코스팅 MAE 집계 및 5사이클 자동 롤백 실시간 연결 (`learner.py`, `runtime.py`, `sensor.py`)**:
  - 코스팅 사이클 완료 시 실측 예측 오차(MAE)와 오버슈트를 `TuningSafetyManager`에 실시간 공급해 5사이클 >30% 성능 저하 자동 롤백이 실제 운전 중 작동하도록 개선
  - `학습 신뢰도` 센서 속성에 `mean_absolute_error`, `comparison_count`, `last_predicted_coast`, `last_observed_coast`, `last_coast_error` 추가 및 `사이클 단계` 센서 속성에 `tau_em_min`, `max_coast_window_min` 노출
- **대상 기기 `unavailable`/`unknown` 60초 유예 가드 (`runtime.py`, `climate.py`, `humidifier.py`)**:
  - 일시적(`60초` 미만) 통신 단절 중에는 기존 `_pwm_state` 및 학습 흐름을 유지하고 `60초` 초과 지속 시에만 `device_unavailable` 오염으로 차단
- **한국어/영어 번역 동기화 및 Stage 6 회귀 테스트 추가 (`strings.json`, `translations/*.json`, `tests/test_stage6_adaptive_enhancements.py`)**

---

## [0.2.5] - 2026-11-21

### 수정 (Fixed)
- **제어기 엔티티(`climate.*` / `humidifier.*`) 기기(Device) 통합 연결 (`climate.py`, `humidifier.py`)**:
  - `SmartPIDThermostatEntity`와 `SmartPIDHumidifierEntity`에 진단 센서(`sensor.py`)와 동일한 `DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})`를 부여하고 `_attr_name = None`으로 설정해, 통합구성요소 기기 화면 내에 제어기 엔티티와 5종 진단 센서가 함께 표시되도록 수정
- **`async_setup_entry` 내 `thermostat_type` 조회 우선순위 수정 (`const.py`, `climate.py`, `humidifier.py`)**:
  - `resolve_thermostat_type(entry)` 헬퍼를 추가해 `entry.options` 최상위·중첩 섹션 → `entry.data` 최상위·중첩 섹션 → `DEFAULT_THERMOSTAT_TYPE` 순으로 조회하도록 개선
- **엔티티 등록 회귀 테스트 추가 (`tests/test_stage1_pid_and_restore.py`)**:
  - `climate_delta` / `switch_pwm` 셋업, `DeviceInfo` 기기 연결, `options` 우선순위 및 주요 속성 조회 예외 없음 검증 추가

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
