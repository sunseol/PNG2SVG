# PNG2SVG / SlideRefine: Figma 독립형 웹 편집기·CLI·AI 도구 전환 계획

> 문서 상태: 구현 기준안(Architecture & Verification Plan)  
> 기준 저장소: `sunseol/PNG2SVG`  
> 기준 브랜치: `main`  
> 기준 커밋: `21c8633a7cc318061f5f6b4875a7cdc51f325bd2`  
> 라이브 점검일: 2026-06-20 (KST)  
> 목표: **Figma 없이 PNG/PPTX를 변환·편집·내보낼 수 있고, 사람과 AI가 동일한 문서/명령 계약을 사용하는 제품으로 전환**

---

## 0. 문서의 판정 원칙

이 문서는 단순한 아이디어 목록이 아니다. 다음 네 종류의 내용을 분리한다.

| 표기 | 의미 | 완료 판정에 사용 여부 |
|---|---|---:|
| **OBSERVED** | 현재 저장소에서 직접 확인한 사실 | 예 |
| **INFERRED** | 확인된 코드 구조에서 합리적으로 도출한 위험 또는 제약 | 보조 근거 |
| **PROPOSED** | 구현할 목표 설계 | 구현 후에만 예 |
| **GATE** | 자동화된 시험과 산출물로 증명해야 하는 조건 | 최종 판정 |

현재 저장소에 대한 라이브 검토는 GitHub의 `main`과 위 커밋에 고정하여 수행했다. 다만 이 검토 환경에서는 저장소를 내려받아 실제 변환을 실행하지 않았으며, 저장소의 최초 커밋 메시지 역시 `--help`만 시험했고 PNG 변환 및 Figma import E2E는 시험하지 않았다고 기록한다. 따라서 현재 런타임 완성도를 추측하지 않고 **미확정**으로 판정한다.[E1]

최종 완료는 문서 체크박스가 아니라 다음 조건으로만 확정한다.

```text
동일 Git SHA
  + CI 전체 통과
  + 고정 fixture E2E 통과
  + 웹 브라우저 E2E 통과
  + MCP 도구 왕복 통과
  + 보안 악성 입력 corpus 통과
  + 성능/품질 기준 통과
  + evidence bundle의 release-verdict.json = pass
```

---

## 1. 결론

현재 프로젝트는 이미 이미지·디렉터리·이미지 기반 PPTX를 받아 SVG를 출력하는 CLI 경로를 갖고 있다. 그러나 제품과 데이터 계약이 Figma handoff에 맞춰져 있고, 설치 가능한 Python 패키지, 중립 문서 모델, 편집 명령 API, 웹 편집기, AI용 기계 인터페이스, 자동화된 E2E 증거가 없다.[E2][E3][E4]

권장 전환 방향은 다음과 같다.

> **Figma importer를 웹으로 복제하지 않는다. 먼저 중립적인 `Scene Document`와 `Document Operation API`를 만들고, CLI·웹 편집기·MCP·Figma exporter를 동일한 애플리케이션 서비스의 클라이언트로 만든다.**

목표 제품 구조는 다음과 같다.

```text
PNG / JPG / WEBP / image-backed PPTX
                 │
                 ▼
┌────────────────────────────────────────┐
│         SlideRefine Conversion Core    │
│ ingest → detect → resolve → reconstruct│
│                → validate              │
└───────────────────┬────────────────────┘
                    │
                    ▼
┌────────────────────────────────────────┐
│      SlideRefine Scene Document (.srf) │
│ nodes + assets + provenance + revision │
└──────────┬────────────┬───────────┬────┘
           │            │           │
           ▼            ▼           ▼
     CLI / SDK      Web Editor     MCP / HTTP
           │            │           │
           └────────────┴───────────┘
                    │
                    ▼
         SVG / PNG / PDF / Figma(optional)
```

구현 단위는 마이크로서비스가 아니라 **모듈형 모놀리스**로 시작한다. 변환 코어, 문서 엔진, 렌더러와 exporter는 한 Python 배포물 안에 두고, 웹 편집기는 HTTP/operation 계약으로 연결한다. Figma는 코어가 아니라 선택적 integration으로 이동한다.

---

## 2. 라이브 저장소 에비던스

### 2.1 저장소 스냅샷

| ID | 확인된 사실 | 판정 | 근거 |
|---|---|---|---|
| E-01 | `main`에는 2026-06-17의 단일 최초 커밋이 있고 전체 SHA는 `21c8633a7cc318061f5f6b4875a7cdc51f325bd2`이다. | OBSERVED | [E1] |
| E-02 | 최초 커밋은 8개 파일, 3,990줄 추가이며, `--help`만 시험했다. E2E PNG 변환과 Figma import는 미시험이라고 명시한다. | OBSERVED | [E1] |
| E-03 | 루트에는 `.gitignore`, `README.md`, `PRODUCT.md`, 단일 Python 스크립트와 Figma plugin 파일만 있다. `pyproject.toml`, 테스트 디렉터리, CI workflow, 명시적 license가 보이지 않는다. | OBSERVED | [E1][E4] |
| E-04 | 제품 문서는 현재 방향을 Figma-first 후처리 도구로 규정한다. | OBSERVED | [E3] |
| E-05 | README는 입력으로 단일 이미지, 이미지 폴더, image-backed PPTX를 명시하며 출력 bundle에 SVG, reference PNG, manifest, `figma-import.json`을 포함한다. | OBSERVED | [E2] |
| E-06 | 현재 CLI는 `--mode package|svg`를 제공한다. 따라서 “CLI에서 SVG 생성”은 부분적으로 이미 존재하지만 설치·계약·자동화 측면은 미완성이다. | OBSERVED | [E2][E5] |
| E-07 | 핵심 Python 파일은 2,864줄(2,471 LOC), 약 101 KB의 단일 파일이다. | OBSERVED | [E6] |
| E-08 | CLI 설명과 기본 mode가 Figma handoff에 직접 결합되어 있다. | OBSERVED | [E5] |
| E-09 | OCR의 특정 한국어 치환 사전이 코어 파일 상수로 하드코딩되어 있다. | OBSERVED | [E7] |
| E-10 | Windows OCR은 PowerShell subprocess를 직접 호출하며 해당 호출에 timeout이 보이지 않는다. | OBSERVED | [E8] |
| E-11 | 텍스트, 래스터, 벡터 결과는 다수의 비정형 `dict`로 전달된다. | OBSERVED | [E9][E10][E11] |
| E-12 | 텍스트 exclusion은 검출 box를 직사각형 mask로 확장하여 픽셀을 제외한다. | OBSERVED | [E10] |
| E-13 | 벡터화는 `RETR_TREE`로 contour를 구하지만 hierarchy를 사용하지 않고 각 contour를 개별 SVG path로 출력한다. | OBSERVED | [E11] |
| E-14 | SVG raster region은 data URI로 내장된다. | OBSERVED | [E12] |
| E-15 | Figma plugin은 JSON 내 Base64 PNG를 디코딩해 `figma.createImage`로 생성한다. | OBSERVED | [E13] |
| E-16 | Figma plugin은 SVG 문자열을 `createNodeFromSvg`에 직접 넘긴다. | OBSERVED | [E14] |
| E-17 | plugin import는 슬라이드를 `for ... of` 루프에서 순차 생성하고 부분 실패 rollback이 보이지 않는다. | OBSERVED | [E15] |
| E-18 | plugin UI는 로컬 JSON의 `slide.name` 등을 `innerHTML`에 삽입하고 package 검증은 format name과 slides 배열 중심의 얕은 검사다. | OBSERVED | [E16] |
| E-19 | Figma manifest는 editor type을 `figma`로 한정한다. | OBSERVED | [E17] |

### 2.2 현재 완성도 판정

| 역량 | 현재 근거 | 현재 판정 |
|---|---|---|
| CLI help 표시 | 최초 커밋 기록상 시험됨 | 확인됨 |
| 단일 이미지 → SVG E2E | README/코드는 존재하나 실행 증거 없음 | 미확정 |
| 이미지 폴더 → SVG E2E | 실행 증거 없음 | 미확정 |
| image-backed PPTX → SVG E2E | 실행 증거 없음 | 미확정 |
| Figma package → Figma import E2E | 최초 커밋에서 명시적으로 미시험 | 미확정 |
| 설치 가능한 독립 CLI | `pyproject.toml`/console entry point 없음 | 부재 |
| 중립 편집 문서 | Figma JSON과 SVG 중심 | 부재 |
| 웹 편집기 | 저장소에 없음 | 부재 |
| AI용 안정적 JSON 명령 | 없음 | 부재 |
| MCP server | 없음 | 부재 |
| 자동 회귀 시험 | 테스트/CI 없음 | 부재 |
| 보안 입력 검증 | plugin UI에 얕은 검증만 확인됨 | 불충분 |

### 2.3 현재 구조에서 바로 도출되는 위험

다음은 OBSERVED 사실에 근거한 INFERRED 위험이다.

1. **리팩터링 회귀를 확인할 기준이 없다.** 최초 커밋 자체가 E2E 미시험이고 curated fixture가 없다.
2. **코어가 Figma와 분리되지 않았다.** CLI의 기본 출력과 메타데이터, plugin JSON, SVG 조립이 한 제품 경로에 묶여 있다.
3. **웹/AI가 수정할 안정적인 객체가 없다.** 전체 SVG 문자열이나 비정형 dict를 직접 수정하면 ID, 좌표, asset, history 계약이 깨지기 쉽다.
4. **파일 크기와 peak memory가 증가한다.** JSON Base64와 SVG data URI는 같은 raster bytes를 여러 형태로 중복 보관할 가능성이 있다.
5. **보안 경계가 약하다.** 임의 JSON 값을 `innerHTML`로 렌더링하며, 문서/asset/좌표/크기/외부 참조에 대한 엄격한 schema gate가 없다.
6. **AI가 안전하게 사용할 수 없다.** AI가 monolithic CLI를 shell로 직접 제어하거나 raw SVG/JSON을 편집해야 하며, revision conflict와 dry-run이 없다.

---

## 3. 목표 범위와 명시적 비범위

### 3.1 이번 계획에서 완료로 확정할 범위

#### A. 독립 CLI

- PNG/JPG/WEBP/BMP/TIFF와 image-backed PPTX를 입력으로 받는다.
- `.srf` 편집 문서 또는 SVG/PNG를 직접 출력한다.
- 설치 가능한 console command `sliderefine`을 제공한다.
- 사람용 출력과 AI용 JSON 출력을 분리한다.
- 동일 입력·옵션에 대해 안정적인 노드 ID와 결정적 산출물을 제공한다.
- 변환, 검증, 조회, 수정, 렌더, export를 모두 CLI에서 수행한다.

#### B. 웹 편집기 MVP

- `.srf`를 열고 저장한다.
- 변환 결과를 웹 canvas에서 선택·이동·크기 조절·회전한다.
- text 내용을 수정한다.
- fill/stroke/opacity/visibility/lock/z-order를 수정한다.
- group/ungroup/duplicate/delete, undo/redo를 지원한다.
- 원본 raster overlay와 낮은 confidence 영역을 검수한다.
- SVG/PNG로 내보낸다.

#### C. AI 사용

- CLI `--json` 계약으로 자동화할 수 있다.
- 동일 application service 위에 MCP stdio server를 제공한다.
- AI는 `convert → inspect/query → apply → render → export`를 수행한다.
- AI는 raw shell, 임의 파일 접근, 임의 SVG script 삽입 권한을 받지 않는다.
- 모든 수정은 revision과 operation ID를 가진 원자적 transaction으로 기록한다.

#### D. 검증 및 증거

- Windows와 Linux CI를 제공한다.
- 고정 fixture corpus와 golden 결과를 제공한다.
- 변환·웹 편집·MCP 왕복 E2E를 자동화한다.
- 보안 malicious corpus와 성능 benchmark를 통과한다.
- 각 release SHA에 대응하는 evidence bundle을 생성한다.

### 3.2 이번 완료 범위에서 제외할 항목

아래 항목은 제품 실패가 아니라 명시적 비범위다.

- 완전한 PowerPoint/Office object semantics 복원
- 모든 사진과 복잡한 illustration의 순수 vector 변환
- Illustrator/Figma 수준의 고급 Bézier control point 편집
- 실시간 다중 사용자 협업과 CRDT
- 브라우저 내부에서 Python/OpenCV/OCR 전체를 WASM으로 실행하는 순수 offline 변환
- 모든 폰트를 자동 식별하고 완벽히 복원하는 기능
- 범용 OCR 학습 또는 모델 hosting

고급 path 편집, collaboration, pure-browser WASM은 Scene Document와 operation API가 안정된 후 별도 roadmap으로 다룬다.

---

## 4. 핵심 아키텍처 결정

### ADR-001 — 편집 원본은 SVG가 아니라 Scene Document다

**결정:** SVG는 export 형식으로 취급하고 편집 원본은 버전된 `.srf` Scene Document로 둔다.

**이유:**

- 전체 SVG 문자열은 개별 object의 provenance, OCR alternative, confidence, history를 안정적으로 보존하기 어렵다.
- 웹 UI와 AI가 동일 node ID를 기준으로 수정해야 한다.
- raster fallback과 editable vector/text를 한 문서에 함께 유지해야 한다.

### ADR-002 — 코어는 Figma를 알지 못한다

```text
domain/application  ─X─> Figma API
export/integrations ───> Figma package 또는 plugin
```

Figma 전용 mode 이름, Base64 asset, plugin data key는 `integrations/figma` 안으로 이동한다.

### ADR-003 — 모듈형 모놀리스로 시작한다

변환, 문서 수정, 렌더링을 별도 network service로 쪼개지 않는다. Python library의 application service를 CLI, local HTTP server, MCP adapter가 직접 호출한다.

### ADR-004 — UI와 AI는 같은 Document Operation을 사용한다

웹 UI가 state를 직접 변경하고 AI가 raw JSON을 변경하는 이중 구현을 금지한다. 모든 변경은 versioned operation schema를 통과한다.

### ADR-005 — 일반 JSON Patch 대신 domain operation을 사용한다

배열 index 기반 `/slides/0/nodes/23` 대신 안정적인 node ID와 의미가 있는 명령을 사용한다.

```json
{
  "type": "set_text",
  "nodeId": "text-07",
  "text": "수정된 제목"
}
```

### ADR-006 — asset은 Base64가 아니라 content-addressed file이다

asset ID는 SHA-256 digest를 사용한다. JSON은 asset metadata와 상대 경로만 보유한다.

### ADR-007 — local-first web editor를 먼저 제공한다

`sliderefine edit input.png`가 localhost service와 브라우저 editor를 실행한다. hosted service는 동일 HTTP 계약을 재사용한다.

### ADR-008 — AI 통합은 CLI 우선, MCP를 얇은 adapter로 추가한다

MCP가 business logic을 가지지 않는다. CLI와 MCP 모두 `ConversionService`, `DocumentService`, `RenderService`를 호출한다.

### ADR-009 — 결정성과 provenance는 기능이다

동일 source digest와 option으로 생성된 source node ID는 재실행해도 안정적이어야 한다. 모든 node는 생성 stage, engine, source region, confidence를 보존한다.

### ADR-010 — fallback은 실패가 아니라 정책이다

vector candidate가 시각 품질 또는 complexity gate를 통과하지 못하면 raster node를 보존한다. 무조건적인 vector화로 path가 폭증하는 것을 금지한다.

---

## 5. 목표 시스템 구조

```text
repo/
├─ pyproject.toml
├─ uv.lock                         # 또는 동등한 Python lock
├─ schema/
│  ├─ document.schema.json
│  ├─ operations.schema.json
│  ├─ conversion-options.schema.json
│  ├─ cli-result.schema.json
│  └─ export-options.schema.json
│
├─ src/sliderefine/
│  ├─ domain/
│  │  ├─ document.py
│  │  ├─ nodes.py
│  │  ├─ geometry.py
│  │  ├─ assets.py
│  │  ├─ diagnostics.py
│  │  └─ operations.py
│  │
│  ├─ application/
│  │  ├─ conversion_service.py
│  │  ├─ document_service.py
│  │  ├─ operation_service.py
│  │  ├─ render_service.py
│  │  └─ validation_service.py
│  │
│  ├─ conversion/
│  │  ├─ ingest.py
│  │  ├─ normalize.py
│  │  ├─ detect_text.py
│  │  ├─ detect_raster.py
│  │  ├─ detect_shape.py
│  │  ├─ resolve_regions.py
│  │  ├─ reconstruct.py
│  │  └─ quality_gate.py
│  │
│  ├─ ports/
│  │  ├─ source_loader.py
│  │  ├─ ocr_engine.py
│  │  ├─ vectorizer.py
│  │  ├─ asset_store.py
│  │  └─ exporter.py
│  │
│  ├─ adapters/
│  │  ├─ sources/
│  │  ├─ ocr/
│  │  ├─ vector/
│  │  ├─ storage/
│  │  └─ cache/
│  │
│  ├─ exporters/
│  │  ├─ srf.py
│  │  ├─ svg.py
│  │  ├─ png.py
│  │  └─ figma.py
│  │
│  ├─ cli/
│  ├─ server/
│  └─ mcp/
│
├─ apps/web/
│  ├─ src/document/
│  ├─ src/commands/
│  ├─ src/renderer/
│  ├─ src/selection/
│  ├─ src/panels/
│  ├─ src/history/
│  └─ src/api/
│
├─ packages/
│  ├─ document-types/              # schema에서 생성된 TypeScript type
│  ├─ document-validator/
│  └─ operation-client/
│
├─ integrations/figma/             # 선택적 호환 계층
├─ tests/
│  ├─ unit/
│  ├─ contract/
│  ├─ integration/
│  ├─ golden/
│  ├─ browser/
│  ├─ mcp/
│  ├─ security/
│  └─ performance/
└─ evidence/
   └─ README.md
```

---

## 6. Scene Document (`.srf`) 설계

### 6.1 container 구조

`.srf`는 MIME type `application/vnd.sliderefine+zip`인 ZIP container로 정의한다.

```text
presentation.srf
├─ manifest.json
├─ document.json
├─ assets/
│  ├─ sha256-0e91....png
│  ├─ sha256-7ac3....webp
│  └─ sha256-e11b....svg
├─ previews/
│  ├─ slide-001.png
│  └─ slide-002.png
└─ diagnostics/
   ├─ document.json
   ├─ slide-001.json
   └─ slide-002.json
```

규칙:

- JSON 안에 Base64 binary를 넣지 않는다.
- ZIP entry path는 `/`, `..`, absolute path를 금지한다.
- asset filename은 content digest에서만 생성한다.
- archive timestamp와 entry ordering을 정규화해 deterministic build를 지원한다.
- `manifest.json`은 모든 entry의 digest와 byte length를 가진다.
- 압축 해제 전에 총 uncompressed size, entry count, compression ratio를 검사한다.

### 6.2 manifest 예시

```json
{
  "format": {
    "name": "slide-refine-document",
    "version": "1.0.0",
    "minimumReaderVersion": "1.0.0"
  },
  "generator": {
    "name": "sliderefine",
    "version": "0.1.0"
  },
  "documentPath": "document.json",
  "documentSha256": "...",
  "assets": {
    "sha256:0e91...": {
      "path": "assets/sha256-0e91....png",
      "mimeType": "image/png",
      "byteLength": 182304,
      "sha256": "0e91..."
    }
  }
}
```

### 6.3 document root

```json
{
  "schemaVersion": "1.0.0",
  "documentId": "doc-01J...",
  "revision": 12,
  "source": {
    "digest": "sha256:...",
    "kind": "image"
  },
  "slides": {
    "slide-001": {
      "id": "slide-001",
      "name": "Slide 1",
      "width": 1920,
      "height": 1080,
      "children": ["background-01", "group-01", "text-07"]
    }
  },
  "nodes": {},
  "assets": {},
  "diagnostics": []
}
```

`slides`와 `nodes`는 ID map으로 두고 z-order는 parent의 `children` 순서로 표현한다. 별도 `zIndex`를 중복 저장하지 않는다.

### 6.4 node 공통 필드

```json
{
  "id": "text-07",
  "type": "text",
  "name": "Title",
  "parentId": "slide-001",
  "transform": [1, 0, 0, 1, 120, 80],
  "opacity": 1,
  "visible": true,
  "locked": false,
  "bounds": { "x": 0, "y": 0, "width": 420, "height": 80 },
  "provenance": {
    "stage": "text_reconstruction",
    "engine": "windows_ocr",
    "sourceRegionId": "region-14",
    "confidence": 0.94
  },
  "extensions": {}
}
```

좌표는 slide coordinate의 float로 통일하고 transform은 SVG/CSS와 호환되는 2D affine 6-tuple을 사용한다. `NaN`, `Infinity`, 음수 width/height를 schema와 domain validation에서 거부한다.

### 6.5 초기 node type

| type | 필수 정보 | MVP 편집 가능 범위 |
|---|---|---|
| `group` | children | 이동, transform, group/ungroup |
| `rect` | width, height, radius, fill, stroke | 전체 |
| `ellipse` | width, height, fill, stroke | 전체 |
| `path` | `d`, fillRule, fill, stroke | 이동/scale/rotate/style; control point 편집은 후속 |
| `text` | text, style, layout, recognition | text/style/layout |
| `image` | assetId, crop, mask, reason | transform, crop, visibility, replace |
| `guide` | geometry, kind | 검수용; export 기본 제외 |

### 6.6 text node

```json
{
  "id": "text-07",
  "type": "text",
  "text": "편집 가능한 제목",
  "style": {
    "fontFamily": "Noto Sans KR",
    "fontSize": 36,
    "fontWeight": 700,
    "lineHeight": 1.2,
    "letterSpacing": 0,
    "align": "left",
    "fill": { "type": "solid", "color": "#111111" }
  },
  "layout": {
    "width": 420,
    "height": 80,
    "overflow": "visible"
  },
  "recognition": {
    "rawText": "편집 가능한 제목",
    "normalizedText": "편집 가능한 제목",
    "language": "ko",
    "confidence": 0.94,
    "status": "usable",
    "alternatives": []
  }
}
```

하드코딩 OCR correction은 core constant가 아니라 다음 계층으로 분리한다.

```text
engine normalization
  → 언어 일반 규칙
  → 사용자 dictionary
  → 프로젝트 dictionary
  → confidence policy
```

항상 `rawText`, correction source, 자동 적용 여부를 보존한다.

### 6.7 path와 hole

현재 코드는 hierarchy contour를 구하고도 각각 독립 path로 출력한다.[E11] 새 vectorizer는 outer contour와 child hole을 하나의 compound path로 구성한다.

```json
{
  "type": "path",
  "d": "M...Z M...Z",
  "fillRule": "evenodd",
  "fill": { "type": "solid", "color": "#FFFFFF" }
}
```

MVP에서는 canonical SVG path `d`를 개별 node geometry로 허용한다. 금지 대상은 **전체 slide SVG 문자열을 하나의 opaque node로 저장하는 것**이다.

### 6.8 안정적 ID

- 변환으로 생성된 source node: source digest + slide ID + stage + normalized region을 UUIDv5/hash 기반으로 생성한다.
- 사람이 신규 생성/복제한 node: UUIDv7 또는 ULID를 사용한다.
- 같은 source와 option을 다시 변환하면 source node ID가 안정적으로 재현되어야 한다.
- operation은 별도 `operationId`로 idempotency를 보장한다.

### 6.9 schema 정책

- JSON Schema Draft 2020-12를 사용한다.[S1]
- core object는 `additionalProperties: false`로 엄격하게 검사한다.
- 확장 필드는 `extensions` map에 namespace를 사용한다.
- Python model과 TypeScript type은 같은 schema에서 생성하거나 contract test로 상호 검증한다.
- major version 변경은 명시적 migrator를 요구한다.

---

## 7. 변환 코어 재구성

### 7.1 pipeline

```text
SourceLoader
  → Normalize
  → Detectors(text/raster/shape/background)
  → Region Resolver
  → Reconstructors(text/vector/raster)
  → Scene Validator
  → Quality Gate
  → Scene Document
```

### 7.2 detector는 확정 node가 아니라 proposal을 반환한다

```python
@dataclass(frozen=True)
class RegionProposal:
    id: str
    kind: Literal["text", "raster", "shape", "background"]
    polygon: Polygon
    confidence: float
    source: str
    features: Mapping[str, float]
```

현재는 text box와 raster mask가 각 단계에서 직접 pixel 소유권을 변경한다.[E10] 목표 구조에서는 detector가 proposal만 반환하고 `RegionResolver`가 overlap, confidence, z-order, fallback을 한곳에서 판정한다.

### 7.3 port

```python
class SourceLoader(Protocol):
    def supports(self, source: Path) -> bool: ...
    def load(self, source: Path) -> Iterable[SourceSlide]: ...

class OcrEngine(Protocol):
    @property
    def name(self) -> str: ...

    def recognize(
        self,
        image: ImageFrame,
        request: OcrRequest,
    ) -> OcrResult: ...

class Vectorizer(Protocol):
    def vectorize(
        self,
        image: ImageFrame,
        region: ResolvedRegion,
        request: VectorizeRequest,
    ) -> VectorizeResult: ...
```

구현 adapter:

```text
ImageSourceLoader
DirectorySourceLoader
ImageBackedPptxSourceLoader
WindowsOcrEngine
HeuristicTextEngine
CompositeOcrEngine
OpenCvVectorizer
FilesystemAssetStore
```

### 7.4 OCR 운영 기준

Windows OCR adapter는 다음을 반드시 제공한다.

- subprocess timeout
- cancellation
- temp file cleanup
- language pack capability report
- stderr/return code를 diagnostic으로 보존
- image digest + language + engine version cache key
- OCR 실패 시 slide 전체가 아니라 해당 region만 fallback

### 7.5 vector quality gate

```text
vector candidate
  ├─ visual error ≤ threshold
  ├─ path count ≤ threshold
  ├─ point count ≤ threshold
  ├─ bounds/alpha/hole validation pass
  └─ finite coordinate validation pass
       → path nodes 채택

otherwise
       → image node fallback + reason diagnostic
```

### 7.6 library-first public API

```python
from pathlib import Path
from sliderefine import ConversionOptions, convert, export, save_document

result = convert(
    Path("slide.png"),
    ConversionOptions(
        preset="editable",
        colors=12,
        simplify=1.5,
        ocr_engine="auto",
    ),
)

save_document(result.document, Path("slide.srf"))
export(result.document, Path("slide.svg"), format="svg")
```

CLI, HTTP, MCP는 이 API를 subprocess로 재호출하지 않고 직접 사용한다.

---

## 8. Document Operation API

### 8.1 transaction envelope

```json
{
  "schemaVersion": "1.0.0",
  "operationId": "op-01J...",
  "expectedRevision": 12,
  "dryRun": false,
  "operations": [
    {
      "type": "set_text",
      "nodeId": "text-07",
      "text": "새로운 제목"
    },
    {
      "type": "translate",
      "nodeIds": ["card-02", "text-08"],
      "dx": 24,
      "dy": 0
    }
  ]
}
```

응답:

```json
{
  "status": "ok",
  "previousRevision": 12,
  "revision": 13,
  "changedNodeIds": ["text-07", "card-02", "text-08"],
  "warnings": [],
  "inverseOperations": []
}
```

### 8.2 MVP operation type

```text
set_text
translate
resize
rotate
set_transform
set_fill
set_stroke
set_opacity
set_visibility
set_locked
reorder
group
ungroup
duplicate
delete
replace_asset
set_name
```

후속 high-level operation:

```text
revectorize_region
replace_ocr_candidate
align_nodes
distribute_nodes
convert_node_type
```

### 8.3 필수 semantics

- transaction은 전부 성공하거나 전부 실패한다.
- `expectedRevision` 불일치는 `REVISION_CONFLICT`로 반환한다.
- 같은 `operationId`를 재전송하면 같은 결과를 반환한다.
- `dryRun`은 수정 없이 validation과 예상 변경을 반환한다.
- 삭제 시 child 정책과 asset reference count를 명시한다.
- 모든 operation은 inverse를 만들거나 snapshot checkpoint를 생성한다.
- UI undo/redo와 AI audit가 같은 operation log를 사용한다.

---

## 9. 독립 CLI 설계

### 9.1 설치

`pyproject.toml`과 console entry point를 추가한다. Python packaging의 console scripts 표준을 따른다.[S2]

```toml
[project]
name = "sliderefine"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "numpy",
  "opencv-python-headless",
  "Pillow"
]

[project.optional-dependencies]
server = ["fastapi", "uvicorn"]
mcp = ["mcp"]
dev = ["pytest", "ruff", "mypy"]

[project.scripts]
sliderefine = "sliderefine.cli.main:main"
```

### 9.2 command surface

```bash
sliderefine doctor
sliderefine capabilities

sliderefine convert input.png -o output.srf
sliderefine convert input.png --format svg -o output.svg

sliderefine inspect output.srf
sliderefine query output.srf --type text --confidence-below 0.8
sliderefine validate output.srf

sliderefine apply output.srf --operations ops.json -o edited.srf
sliderefine render edited.srf --slide slide-001 -o preview.png
sliderefine export edited.srf --format svg -o result.svg

sliderefine edit input.png
sliderefine serve
sliderefine mcp --transport stdio
sliderefine benchmark tests/fixtures/manifest.json
```

### 9.3 사람용과 기계용 출력

AI가 사용할 때 stdout에는 오직 versioned JSON 결과만 출력한다.

```bash
sliderefine convert slide.png -o slide.srf --json
```

```json
{
  "apiVersion": "sliderefine.cli/v1",
  "status": "ok",
  "command": "convert",
  "documentUri": "file:///workspace/slide.srf",
  "revision": 1,
  "slides": 1,
  "warnings": [
    {
      "code": "TEXT_LOW_CONFIDENCE",
      "nodeId": "text-07"
    }
  ],
  "metrics": {
    "durationMs": 1832,
    "peakRssBytes": 284000000
  }
}
```

규칙:

- `--json`: final result는 stdout, log는 stderr
- `--log-format json`: stderr log도 JSONL
- progress bar는 TTY에서만 표시
- 경로를 추측하게 하지 않고 모든 artifact URI를 응답에 포함
- error도 같은 envelope와 stable `code`를 사용

### 9.4 exit code

| code | 의미 |
|---:|---|
| 0 | 성공 |
| 2 | CLI usage 오류 |
| 3 | 입력 파일/형식 오류 |
| 4 | 변환 실패 |
| 5 | 문서/schema validation 실패 |
| 6 | revision 또는 output conflict |
| 7 | timeout/cancel |
| 8 | security policy 거부 |
| 70 | 내부 오류 |

### 9.5 안전 option

```text
--workspace-root
--overwrite
--timeout
--max-image-pixels
--max-slides
--max-output-bytes
--no-network
--deterministic
--dry-run
```

### 9.6 기존 command 호환

기존 진입점은 즉시 삭제하지 않는다.

```python
# vectorize_ppt_svg.py
from sliderefine.cli.legacy import legacy_main

if __name__ == "__main__":
    raise SystemExit(legacy_main())
```

기존 `--mode svg`와 package mode를 새 application service로 전달하고 deprecation warning을 stderr에만 출력한다.

---

## 10. 웹 편집기

### 10.1 MVP 구성

```text
Document Store
   │
   ├─ Native SVG Scene Renderer
   ├─ Selection/Transform Overlay
   ├─ Layer Tree
   ├─ Inspector Panels
   ├─ Original Overlay
   └─ Command Bus
        ├─ optimistic apply
        ├─ revision validation
        ├─ undo/redo
        └─ autosave
```

권장 구현은 TypeScript 기반 SPA와 native SVG DOM이다. 첫 버전에서 Fabric/Konva 같은 canvas abstraction에 문서 모델을 종속시키지 않는다.

### 10.2 state 분리

```typescript
interface DocumentState {
  documentId: string;
  revision: number;
  slides: Record<string, Slide>;
  nodes: Record<string, SceneNode>;
  assets: Record<string, AssetMetadata>;
}

interface EditorState {
  activeSlideId: string;
  selectedNodeIds: string[];
  zoom: number;
  viewport: { x: number; y: number };
  tool: "select" | "text" | "shape" | "pan";
  originalOverlayOpacity: number;
}
```

`.srf`에는 `DocumentState`만 저장한다. selection, zoom, panel 상태는 editor local state다.

### 10.3 렌더링

- `path`, `rect`, `ellipse`, `image`, `text`를 개별 SVG element로 렌더한다.
- selection box와 handle은 별도 overlay layer에 둔다.
- node transform은 document의 affine matrix를 그대로 사용한다.
- text 편집은 SVG `<text>`의 직접 contenteditable 대신 위치가 맞는 HTML textarea overlay로 수행한다.
- asset은 digest별 object URL/cache를 사용한다.
- 전체 document 변경이 아니라 changed node만 다시 렌더한다.

### 10.4 기능 gate

MVP에 반드시 포함:

- 파일 open/save
- slide switch
- zoom/pan
- 단일/다중 선택
- move/resize/rotate
- layer reorder
- group/ungroup
- duplicate/delete
- fill/stroke/opacity
- visibility/lock
- text content/style
- undo/redo
- original overlay
- confidence filter
- SVG/PNG export

MVP 이후:

- Bézier point editing
- snap/guides 고도화
- component/reusable asset
- collaboration

### 10.5 local editor mode

```bash
sliderefine edit slide.png
```

동작:

1. source를 temporary `.srf`로 변환한다.
2. `127.0.0.1`에 local service를 연다.
3. random access token이 포함된 browser URL을 연다.
4. editor는 HTTP operation API로 문서를 수정한다.
5. autosave와 export를 제공한다.

보안:

- loopback에만 bind
- 실행마다 random token
- exact Origin 검사
- workspace 밖 경로 거부
- 외부 URL fetch 기본 금지
- idle shutdown
- temp cleanup

### 10.6 HTTP API

OpenAPI 3.1+ 문서로 계약을 공개한다. OpenAPI는 언어 중립적인 HTTP interface description을 제공한다.[S3]

```text
POST /api/v1/conversions
GET  /api/v1/conversions/{jobId}
GET  /api/v1/documents/{documentId}
GET  /api/v1/documents/{documentId}/nodes
GET  /api/v1/assets/{digest}
POST /api/v1/documents/{documentId}/operations
POST /api/v1/documents/{documentId}/render
POST /api/v1/documents/{documentId}/export
```

local mode는 conversion을 동기 실행할 수 있지만 hosted mode는 job ID와 progress를 사용한다. 문서 수정 endpoint는 `expectedRevision`과 `Idempotency-Key`를 요구한다.

---

## 11. AI 도구 설계

### 11.1 AI readiness의 조건

MCP wrapper만 추가해서는 AI-ready가 아니다. 다음 조건이 먼저 충족되어야 한다.

- stable node ID
- strict schema
- query 가능한 compact summary
- atomic operation
- revision conflict
- dry-run
- deterministic preview
- resource URI
- workspace sandbox
- structured error code

### 11.2 MCP transport

로컬 AI client에는 stdio, 원격 hosted service에는 Streamable HTTP를 제공한다. MCP 2025-11-25 specification은 두 방식을 표준 transport로 정의한다.[S4] 공식 Python SDK는 tool/resource와 해당 transport를 제공한다.[S5]

### 11.3 초기 tool set

#### `convert_image`

```json
{
  "inputUri": "file:///workspace/slide.png",
  "outputFormat": "srf",
  "options": {
    "preset": "editable",
    "ocrEngine": "auto",
    "colors": 12
  }
}
```

#### `inspect_document`

```json
{
  "documentUri": "file:///workspace/slide.srf",
  "include": ["summary", "warnings", "text"]
}
```

#### `query_nodes`

```json
{
  "documentUri": "file:///workspace/slide.srf",
  "slideId": "slide-001",
  "types": ["text", "image", "path"],
  "confidenceBelow": 0.8,
  "limit": 100,
  "cursor": null
}
```

#### `apply_operations`

```json
{
  "documentUri": "file:///workspace/slide.srf",
  "expectedRevision": 4,
  "dryRun": false,
  "operations": [
    {
      "type": "set_text",
      "nodeId": "text-03",
      "text": "수정된 제목"
    }
  ]
}
```

#### `render_preview`

```json
{
  "documentUri": "file:///workspace/slide.srf",
  "slideId": "slide-001",
  "scale": 1,
  "overlay": "none"
}
```

#### `export_document`

```json
{
  "documentUri": "file:///workspace/slide.srf",
  "format": "svg",
  "outputUri": "file:///workspace/result.svg"
}
```

### 11.4 응답 크기 정책

큰 binary를 Base64 tool response로 반환하지 않는다. MCP resource URI 또는 file URI를 반환한다.

```json
{
  "revision": 5,
  "preview": {
    "uri": "sliderefine://documents/doc-01/previews/slide-001.png",
    "mimeType": "image/png",
    "size": 284231,
    "sha256": "..."
  }
}
```

MCP tool은 name과 input schema를 통해 model이 기능을 발견하게 하며, resource는 URI로 context를 제공한다.[S6]

### 11.5 AI의 표준 feedback loop

```text
convert_image
   ↓
inspect_document / query_nodes
   ↓
apply_operations(dryRun=true)
   ↓
apply_operations
   ↓
render_preview
   ↓
preview 시각 검토
   ↓
추가 수정
   ↓
export_document
```

### 11.6 금지 tool

```text
execute_shell
run_arbitrary_command
read_any_file
write_any_path
download_arbitrary_url
inject_raw_html
inject_raw_script
```

모든 URI는 workspace root 아래로 canonicalize한 뒤 다시 검사한다. symlink escape, `..`, alternate data stream, ZIP traversal을 차단한다.

---

## 12. 보안 설계

### 12.1 입력 제한

- 최대 source file size
- 최대 image pixel count
- 최대 slide count
- 최대 archive entry count
- 최대 uncompressed bytes
- 최대 compression ratio
- 최대 SVG/path command count
- 최대 document node count와 depth
- parse/convert timeout

### 12.2 SVG

import/export 시 다음을 제거 또는 거부한다.

```text
script
foreignObject
on* event attribute
external href
javascript: URI
remote font/image
entity expansion
filter/clip recursion 폭주
비정상적으로 깊은 group
NaN/Infinity coordinate
```

### 12.3 web UI

현재 plugin UI의 `innerHTML` 패턴은 제거 대상이다.[E16]

- 외부 문서 값은 `textContent` 또는 framework escaping으로만 렌더한다.
- HTML/SVG raw injection API를 공개하지 않는다.
- local service는 CSP, exact Origin, token 검사를 적용한다.

### 12.4 subprocess

- `shell=True` 금지
- command argument array 사용
- timeout/cancel
- 제한된 environment
- temp directory 격리
- stdout/stderr byte limit
- child process tree cleanup

### 12.5 supply chain

- Python wheel과 web bundle dependency lock
- dependency vulnerability scan
- SBOM 생성
- release artifact SHA-256
- license/NOTICE 정책 확정

현재 snapshot에는 명시적 LICENSE가 보이지 않으므로 public distribution 전에 license 상태를 결정하는 gate를 추가한다.[E1]

---

## 13. 시험 데이터와 품질 지표

### 13.1 fixture corpus

최소 corpus category:

```text
shape-heavy
text-heavy-ko
text-heavy-en
mixed-language
cards-and-panels
icons
thin-strokes
rounded-rectangles
transparency
gradients
photos
dense-illustration
charts
image-backed-pptx
malformed-input
malicious-archive
malicious-svg
```

각 fixture에 다음 manifest를 둔다.

```json
{
  "id": "ko-title-card-01",
  "source": "ko-title-card-01.png",
  "expected": {
    "conversion": "success",
    "requiredText": ["제품 소개"],
    "minimumTextNodes": 2,
    "maximumPathNodes": 600,
    "mustPreserveRasterRegions": true
  }
}
```

현재 초기 commit은 810 MB generated tmp를 제외했고 curated sample은 추가되지 않았다.[E1] 따라서 작은 redistributable fixture와 별도의 private benchmark corpus를 분리한다.

### 13.2 시험 계층

| 계층 | 검증 |
|---|---|
| unit | geometry, ID, asset digest, operation inverse, validator |
| contract | JSON Schema ↔ Python ↔ TypeScript |
| integration | source loader, OCR adapter, vectorizer, exporter |
| golden | 고정 source의 document/preview 구조 회귀 |
| browser E2E | open, select, move, text edit, undo, save, export |
| MCP E2E | list tools, convert, query, apply, render, export |
| security | traversal, zip bomb, SVG script, huge dimensions, malformed JSON |
| performance | time, peak RSS, package size, node/path count |

### 13.3 품질 metric

#### 기능

- conversion success/error classification
- schema validation rate
- orphan/cycle/invalid transform count
- deterministic artifact hash

#### 시각

- SSIM
- normalized pixel difference
- alpha edge difference
- slide/region별 visual diff image

#### text

- CER
- word accuracy
- box IoU
- text node recall/precision
- correction auto-apply accuracy

#### editability

- text로 복원된 character ratio
- raster area ratio
- path node 수
- control point 수
- group depth
- node count

#### 성능

- stage별 duration
- p50/p95 conversion time
- peak RSS
- package bytes
- asset dedup ratio
- browser initial render와 interaction latency

### 13.4 release threshold

초기 baseline을 먼저 기록한 후 다음 floor와 non-regression을 동시에 적용한다.

| Gate | 기준 |
|---|---|
| Core fixture | 모든 success fixture가 성공하고 expected failure는 typed error로 종료 |
| Schema | 0 violation, 0 orphan, 0 cycle, 0 non-finite coordinate |
| Determinism | 같은 환경·source·option 3회 실행의 canonical document와 asset hash 동일 |
| Visual editable preset | corpus median SSIM ≥ 0.92, p10 ≥ 0.82, 기존 baseline보다 악화 금지 |
| Visual fidelity preset | corpus median SSIM ≥ 0.98; 실패 region은 raster fallback 허용 |
| Text | 지원 OCR 환경에서 CER가 baseline 이하이며 fixed OCR fixture CER ≤ 0.20 |
| Complexity | fixture manifest의 max node/path/control-point 한도 준수 |
| Performance | p95 duration과 peak RSS가 고정 baseline 대비 15% 이상 악화되면 승인 필요 |
| Browser | 필수 user journey 100% 통과 |
| MCP | 표준 왕복 scenario 100% 통과, stdout protocol 오염 0건 |
| Security | malicious corpus 100% 거부, workspace escape 0건 |

절대 threshold가 현실 데이터와 맞지 않으면 Phase 0의 raw report를 근거로 한 차례 조정할 수 있다. 단, threshold를 낮추는 PR에는 before/after evidence와 승인 기록이 있어야 한다.

---

## 14. Evidence bundle — 완성도를 확정하는 산출물

각 release candidate는 다음 directory를 생성한다.

```text
evidence/<git-sha>/
├─ manifest.json
├─ environment.json
├─ commands.jsonl
├─ source-tree.txt
├─ hashes.sha256
├─ unit/junit.xml
├─ contract/report.json
├─ golden/report.json
├─ visual/
│  ├─ metrics.json
│  ├─ original/
│  ├─ rendered/
│  └─ diff/
├─ browser/playwright-report/
├─ mcp/
│  ├─ inspector-transcript.jsonl
│  └─ scenario-report.json
├─ security/report.json
├─ performance/benchmark.json
├─ packages/
│  ├─ wheel.sha256
│  └─ web-bundle.sha256
└─ release-verdict.json
```

`release-verdict.json` 예시:

```json
{
  "gitSha": "...",
  "generatedAt": "2026-06-20T00:00:00Z",
  "gates": {
    "unit": "pass",
    "contract": "pass",
    "cliE2E": "pass",
    "golden": "pass",
    "visual": "pass",
    "browser": "pass",
    "mcp": "pass",
    "security": "pass",
    "performance": "pass"
  },
  "exceptions": [],
  "overall": "pass"
}
```

완료 판정 규칙:

1. `gitSha`가 release artifact의 source SHA와 같아야 한다.
2. `exceptions`가 있으면 owner, reason, expiry, issue가 필요하다.
3. P0/P1 gate exception은 허용하지 않는다.
4. 사람이 작성한 README 문구만으로 pass로 바꿀 수 없다.
5. CI에서 생성하지 않은 evidence bundle은 release 근거로 인정하지 않는다.

---

## 15. 구현 순서와 PR별 완료 증거

기간이 아니라 dependency 순서로 진행한다.

### PR-00 — Baseline과 안전망

**변경**

- curated fixture 추가
- current script를 호출하는 baseline harness
- Windows/Linux CI
- ruff, type checker, pytest 기본 설정
- current CLI `--help` snapshot
- current SVG/package E2E 실행 및 결과 hash
- Figma E2E가 자동화 불가하면 수동 protocol과 screen recording template부터 추가
- UI `innerHTML` 제거와 package size guard 즉시 수정
- subprocess timeout 추가

**완료 증거**

```text
baseline/current/<fixture>/...
baseline-report.json
CI Windows pass
CI Linux pass
```

**Gate:** 현재 runtime의 실제 성공/실패가 fixture별로 기록되기 전에는 구조 리팩터링을 시작하지 않는다.

### PR-01 — 설치 가능한 package와 library seam

**변경**

- `pyproject.toml`
- `src/sliderefine`
- `ConversionOptions`, `ConversionResult`
- 기존 script를 `legacy_pipeline`로 이동
- `sliderefine convert` entry point
- dependency와 license 정책

**완료 증거**

```bash
python -m build
pipx install dist/*.whl
sliderefine --help
sliderefine convert fixture.png --format svg -o out.svg
```

**Gate:** clean environment에서 wheel만 설치해 변환 가능.

### PR-02 — Scene Document v1과 legacy adapter

**변경**

- document/operation JSON Schema
- typed Python model
- schema-generated 또는 contract-tested TypeScript type
- legacy dict → Scene Document adapter
- `.srf` reader/writer
- content-addressed asset
- deterministic ZIP

**완료 증거**

```bash
sliderefine convert fixture.png -o fixture.srf
sliderefine validate fixture.srf --json
sliderefine inspect fixture.srf --json
```

**Gate:** 모든 fixture document가 schema와 domain invariant를 통과하고 3회 hash가 일치.

### PR-03 — Exporter와 CLI v1 완성

**변경**

- SVG exporter
- PNG renderer
- `inspect`, `query`, `validate`, `render`, `export`
- machine JSON envelope
- stable exit code
- limits/timeout/workspace policy
- 기존 CLI compatibility wrapper

**완료 증거**

```text
CLI contract test
shell scenario transcript
Windows/Linux wheel smoke test
```

**Gate:** Figma 없이 input → `.srf` → SVG/PNG roundtrip 통과.

### PR-04 — Operation engine

**변경**

- transaction, revision, idempotency
- MVP operation
- inverse operation/undo
- `sliderefine apply`
- audit log

**완료 증거**

```bash
sliderefine apply fixture.srf --operations ops.json -o edited.srf --json
sliderefine render edited.srf --slide slide-001 -o edited.png
```

**Gate:** operation unit/property test, revision conflict, dry-run, undo roundtrip 100% 통과.

### PR-05 — Web renderer와 editor MVP

**변경**

- document client
- native SVG renderer
- selection overlay
- transform
- layer tree
- text/style editing
- undo/redo
- original overlay
- export UI

**완료 증거**

```text
Playwright HTML report
journey별 screenshot/video
reload 후 state persistence hash
```

**Gate:** browser E2E 필수 journey 전체 통과.

### PR-06 — Local editor service

**변경**

- `sliderefine edit`
- loopback HTTP service
- token/origin/workspace guard
- autosave
- conversion progress
- clean shutdown

**완료 증거**

```bash
sliderefine edit fixture.png --headless-e2e
```

**Gate:** browser에서 PNG open → text 변경 → 이동 → 저장 → 재열기 → export가 자동 E2E로 동일하게 재현.

### PR-07 — MCP adapter

**변경**

- stdio server
- six initial tools
- resource URI
- structured content/error
- progress/cancel
- workspace sandbox
- optional Streamable HTTP

**완료 증거**

```text
MCP Inspector tool list
convert/query/apply/render/export transcript
invalid path/revision/security test
```

**Gate:** AI standard feedback loop 100% 통과, stdout에 protocol 이외 출력 0건.

### PR-08 — 품질·보안·성능 hardening

**변경**

- malicious corpus
- fuzz/property tests
- visual metric report
- cache
- asset dedup
- slide process parallelism
- memory streaming
- SBOM와 release evidence bundle

**완료 증거**

`release-verdict.json = pass`

### PR-09 — Figma를 optional integration으로 이동

**변경**

- Scene Document → Figma exporter/importer
- 기존 package reader migration
- TypeScript와 strict schema
- Base64 제거 가능한 경로
- slide-level rollback

**Gate:** Figma가 설치되지 않은 환경에서도 모든 core/CLI/web/MCP test가 통과한다.

---

## 16. Release 수준별 Definition of Done

### R1 — Standalone CLI Complete

다음이 모두 pass일 때만 완료다.

- wheel 설치 후 `sliderefine` 실행
- single image, directory, image-backed PPTX fixture 변환
- `.srf`, SVG, PNG output
- strict schema와 deterministic hash
- inspect/query/apply/render/export
- AI-friendly JSON와 exit code
- Windows/Linux CI
- Figma dependency 0

### R2 — Web Editor MVP Complete

R1에 더해:

- `.srf` open/save
- selection/transform/layer/text/style/group/undo
- original overlay
- browser E2E
- local `sliderefine edit`
- workspace/origin/token security
- SVG/PNG export

### R3 — AI Tool Complete

R2에 더해:

- MCP stdio tool discovery
- six standard tools
- revision/dry-run/idempotency
- resource URI preview
- full feedback loop E2E
- path and file sandbox
- structured error self-correction 가능

### R4 — Optional Figma Compatibility

R3의 완성 조건과 독립적이다. Figma integration 실패가 core release를 막지 않되, Figma 호환을 release feature로 표시할 경우 별도 E2E evidence가 필요하다.

---

## 17. 정확한 완료 검증 command

아래 command set을 release CI와 동일하게 실행한다.

```bash
# Static and unit
python -m ruff check .
python -m mypy src
python -m pytest tests/unit tests/contract -q

# Build and clean install
python -m build
pipx install --force dist/*.whl
sliderefine doctor --json
sliderefine capabilities --json

# CLI roundtrip
sliderefine convert tests/fixtures/shape-heavy.png \
  -o .artifacts/shape-heavy.srf --deterministic --json
sliderefine validate .artifacts/shape-heavy.srf --json
sliderefine inspect .artifacts/shape-heavy.srf --json
sliderefine query .artifacts/shape-heavy.srf --type text --json
sliderefine apply .artifacts/shape-heavy.srf \
  --operations tests/fixtures/ops/edit-title.json \
  -o .artifacts/shape-heavy-edited.srf --json
sliderefine render .artifacts/shape-heavy-edited.srf \
  --slide slide-001 -o .artifacts/preview.png --json
sliderefine export .artifacts/shape-heavy-edited.srf \
  --format svg -o .artifacts/result.svg --json

# Reproducibility
sliderefine convert tests/fixtures/shape-heavy.png \
  -o .artifacts/run-a.srf --deterministic --json
sliderefine convert tests/fixtures/shape-heavy.png \
  -o .artifacts/run-b.srf --deterministic --json
sha256sum .artifacts/run-a.srf .artifacts/run-b.srf

# Browser
npm --prefix apps/web test
npm --prefix apps/web run test:e2e

# MCP
sliderefine mcp --transport stdio
# 별도 test client가 tools/list와 표준 scenario를 실행

# Security and performance
python -m pytest tests/security -q
sliderefine benchmark tests/fixtures/manifest.json \
  --output evidence/$GIT_SHA/performance/benchmark.json

# Final verdict
python -m tools.release_gate \
  --evidence evidence/$GIT_SHA \
  --output evidence/$GIT_SHA/release-verdict.json
```

---

## 18. 초기 issue backlog

| ID | 제목 | 선행 조건 | 완료 산출물 |
|---|---|---|---|
| SR-001 | current E2E baseline 확보 | 없음 | baseline report |
| SR-002 | pyproject와 wheel packaging | SR-001 | install smoke evidence |
| SR-003 | Scene Document schema v1 | SR-002 | schema + fixtures |
| SR-004 | legacy pipeline adapter | SR-003 | golden non-regression |
| SR-005 | deterministic asset bundle | SR-003 | hash repeat test |
| SR-006 | standalone CLI command set | SR-004 | CLI contract report |
| SR-007 | operation engine | SR-003 | operation property tests |
| SR-008 | SVG/PNG renderer | SR-003 | visual report |
| SR-009 | web scene renderer | SR-007, SR-008 | browser rendering report |
| SR-010 | web editor MVP | SR-009 | Playwright report |
| SR-011 | local editor service | SR-010 | local E2E |
| SR-012 | MCP stdio adapter | SR-006, SR-007, SR-008 | MCP scenario report |
| SR-013 | security hardening | 전 단계 | security report |
| SR-014 | performance/cache | baseline 이후 | benchmark report |
| SR-015 | optional Figma adapter | R1 이후 | Figma-specific evidence |

---

## 19. 주요 위험과 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| legacy output이 실제로 동작하지 않을 수 있음 | baseline 자체 부재 | PR-00에서 성공/실패를 먼저 기록하고 typed expected failure로 분리 |
| Scene model이 너무 빨리 복잡해짐 | 구현 정체 | MVP node type만 도입, advanced path semantics 후속 |
| 웹에서 text layout이 OS/font마다 다름 | visual drift | font fallback 기록, bundled/open font 정책, reference overlay, text metric test |
| path 수 폭증 | editor 성능 저하 | vector quality gate와 raster fallback |
| Base64 migration 중 package 호환 깨짐 | 기존 plugin 사용자 영향 | v1 reader와 migration exporter를 한 release 이상 유지 |
| OCR 플랫폼 의존 | Windows 외 결과 차이 | OcrEngine port, capability report, OCR-none deterministic mode |
| AI가 큰 document를 한 번에 읽음 | token/latency 증가 | query pagination, summary, filtered node response |
| AI가 stale revision 수정 | 데이터 손실 | expectedRevision과 conflict response |
| local web server 탈취 | 파일 접근 위험 | loopback/token/origin/workspace sandbox |
| 임의 SVG/ZIP 공격 | XSS/DoS/path traversal | sanitizer, limits, malicious corpus |
| 테스트 threshold가 자의적임 | 품질 판정 논쟁 | baseline raw report를 저장하고 threshold 변경에 evidence/approval 요구 |

---

## 20. 최종 승인 체크리스트

### Architecture

- [ ] domain/application이 Figma API나 Figma package field를 import하지 않는다.
- [ ] CLI, HTTP, MCP가 같은 application service를 호출한다.
- [ ] UI와 AI가 같은 operation schema를 사용한다.
- [ ] SVG는 exporter이고 `.srf`가 편집 source of truth다.

### CLI

- [ ] clean wheel install
- [ ] stable JSON envelope
- [ ] documented exit code
- [ ] deterministic mode
- [ ] workspace/size/timeout guard
- [ ] direct SVG output

### Document

- [ ] JSON Schema pass
- [ ] asset digest verification
- [ ] no Base64 in document JSON
- [ ] no orphan/cycle/non-finite coordinate
- [ ] migration test

### Web

- [ ] required edit journeys pass
- [ ] no raw `innerHTML` for document data
- [ ] original overlay
- [ ] undo/redo persistence
- [ ] export roundtrip

### AI

- [ ] MCP tools listed
- [ ] stdio protocol stdout clean
- [ ] dry-run/revision/idempotency
- [ ] preview resource URI
- [ ] workspace escape rejected
- [ ] standard feedback loop pass

### Evidence

- [ ] evidence SHA = release SHA
- [ ] visual diff and metrics retained
- [ ] browser report retained
- [ ] MCP transcript retained
- [ ] security report retained
- [ ] performance report retained
- [ ] `release-verdict.json.overall == "pass"`

---

## 21. 최종 권고

가장 먼저 웹 화면부터 만들면 editor가 현재 Figma package와 SVG 문자열에 다시 결합된다. 가장 먼저 MCP wrapper부터 만들면 AI가 monolithic script와 raw file을 불안정하게 조작하게 된다.

올바른 순서는 다음이다.

```text
1. current E2E evidence 확보
2. installable library/CLI seam
3. Scene Document + schema
4. operation engine
5. standalone CLI complete
6. web editor
7. local server
8. MCP adapter
9. hardening과 evidence release
10. Figma optional integration
```

이 순서로 구현하면 최종 제품은 다음 세 경로를 모두 같은 코어로 제공한다.

```text
사람 — CLI
  sliderefine convert input.png --format svg -o output.svg

사람 — Web
  sliderefine edit input.png

AI — CLI 또는 MCP
  convert → inspect/query → apply → render → export
```

완료의 기준은 “화면이 보인다” 또는 “명령이 한 번 실행된다”가 아니다. **고정 SHA의 evidence bundle이 CLI·웹·AI·보안·성능 gate를 모두 통과하는 것**이다.

---

## 근거 링크

### 저장소 라이브 에비던스

- [E1] 최초 커밋, 전체 SHA, 파일 트리, 시험/미시험 기록: https://github.com/sunseol/PNG2SVG/commit/21c8633a7cc318061f5f6b4875a7cdc51f325bd2
- [E2] README의 Figma-first workflow, 입력/출력과 current CLI: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/README.md
- [E3] PRODUCT.md의 Figma-first positioning과 MVP boundary: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/PRODUCT.md#L1-L43
- [E4] repository root tree: https://github.com/sunseol/PNG2SVG/tree/21c8633a7cc318061f5f6b4875a7cdc51f325bd2
- [E5] current CLI parse와 `package|svg`: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py#L41-L125
- [E6] 핵심 Python file metadata(2,864 lines): https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py
- [E7] hard-coded OCR replacements: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py#L31-L39
- [E8] Windows OCR와 PowerShell subprocess: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py#L478-L552
- [E9] heuristic text candidate dict와 MSER: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py#L303-L360
- [E10] OCR normalization과 text exclusion mask: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py#L573-L623
- [E11] raster detection, vectorization dict와 contour 처리: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py#L624-L782
- [E12] raster region data URI SVG 삽입: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/vectorize_ppt_svg.py#L797-L807
- [E13] Figma Base64 decode와 image 생성: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/figma-plugin/slide-refine-importer/code.js#L127-L222
- [E14] `createNodeFromSvg` 직접 호출: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/figma-plugin/slide-refine-importer/code.js#L378-L417
- [E15] 순차 slide import loop: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/figma-plugin/slide-refine-importer/code.js#L428-L477
- [E16] UI `innerHTML`과 얕은 package validation: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/figma-plugin/slide-refine-importer/ui.html#L219-L270
- [E17] Figma-only manifest: https://github.com/sunseol/PNG2SVG/blob/21c8633a7cc318061f5f6b4875a7cdc51f325bd2/figma-plugin/slide-refine-importer/manifest.json#L1-L7

### 표준과 구현 근거

- [S1] JSON Schema Draft 2020-12: https://json-schema.org/draft/2020-12
- [S2] Python console entry points: https://packaging.python.org/specifications/entry-points/
- [S3] OpenAPI Specification: https://spec.openapis.org/oas/
- [S4] MCP 2025-11-25 transports: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- [S5] Official MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- [S6] MCP tools specification: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
