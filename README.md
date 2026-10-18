# Smart PID Airconditioner

Home Assistant 기반의 스마트 PID 제어 에어컨 및 가상 테스트 환경 연동 프로젝트입니다.

---

## 📌 프로젝트 개요
실제 에어컨/가습기 제어 및 PID 기반 자동화 알고리즘 테스트를 위해 Home Assistant에 가상의 공조 장치(`climate.test`, `humidifier.test`)를 MQTT Discovery로 등록하고, 실시간 제어 및 피드백 상태를 시뮬레이션할 수 있는 환경을 제공합니다.

---

## 🛠️ 가상 장치 구성 사양

### 1. 가상 에어컨 (`climate.test`)
- **설정 파일**: [`devices/climate_test_discovery.json`](devices/climate_test_discovery.json)
- **MQTT Discovery 토픽**: `homeassistant/climate/test_climate/config`
- **지원 운전 모드 (`modes`)**:
  - `off`
  - `cool` (냉방)
  - `dry` (제습)
  - `fan_only` (송풍)
  - `heat` (난방)
  - `auto` (자동)
- **온도 범위**: 16.0°C ~ 30.0°C (0.5°C 단위 조절)
- **풍량 모드 (`fan_modes`)**: `low`, `medium`, `high`, `auto`

### 2. 가상 가습기 / 제습기 (`humidifier.test`)
- **설정 파일**: [`devices/humidifier_test_discovery.json`](devices/humidifier_test_discovery.json)
- **MQTT Discovery 토픽**: `homeassistant/humidifier/test_humidifier/config`
- **목표 습도 범위**: 30% ~ 80%
- **지원 운전 모드 (`modes`)**:
  - `normal` (일반)
  - `eco` (절전)
  - `sleep` (수면)
  - `turbo` (터보/쾌속)
  - `auto` (자동)

---

## 🔄 루프백 자동화 (Loopback Automations)
- **자동화 설정 파일**: [`automations/virtual_devices_loopback.yaml`](automations/virtual_devices_loopback.yaml)
- 가상 장치에 전송된 명령(Command Topic)을 즉시 상태(State Topic)로 피드백하여 HA 대시보드 UI 상에서 실제 기기처럼 즉각 반응하도록 처리합니다.

---

## 🤖 에이전트 스킬 (Agent Skills)
- [`.agents/skills/home-assistant-best-practices`](.agents/skills/home-assistant-best-practices/SKILL.md)
  - Home Assistant 자동화, 헬퍼 구성, 템플릿 센서, AppDaemon 모범 사례 가이드 및 레퍼런스 모음
