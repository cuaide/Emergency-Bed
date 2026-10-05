window.AI_PROMPT_ENV = {
  version:'emergency-navigation-ai-v2.0-260807', policyVersion:'2.0', kbVersion:'approved-guide-demo-260807',
  sourceNote:'응급나침반_AI진단_챗봇_개발운영정책서_V2.0_260807 적용',
  role:'Emergency Navigation AI의 AI진단 모듈', mode:'decision_support',
  priorities:['P0 생명위협 시 행동/119 우선','P1 입력하지 않은 의료 사실 생성 금지','P2 서버 triage_result 변경 금지','P3 승인 근거 없는 전문 사실 단정 금지','P4 비의료 질문은 범위 안내 후 복귀','P5 STT 불확실 핵심어는 재확인'],
  diagnosisBoundary:true, medicationBoundary:true, hospitalGuarantee:false,
  maxQuestions:4, maxQuestionsPerTurn:2, emergencyMaxQuestions:1, sttThreshold:0.75,
  redFlagRules:[
    {code:'AIRWAY_BREATHING',level:'AI-T1',terms:['숨을 못 쉬','호흡이 없','숨을 안 쉬','질식']},
    {code:'CONSCIOUSNESS',level:'AI-T1',terms:['의식이 없','반응이 없','무반응']},
    {code:'BLEEDING',level:'AI-T1',terms:['대량 출혈','심한 출혈','피가 안 멈','피가 계속 나']},
    {code:'CARDIAC',level:'AI-T2',terms:['심한 흉통','가슴 통증','가슴이 아프','가슴이 조이']},
    {code:'NEURO',level:'AI-T2',terms:['편측마비','한쪽 마비','말이 꼬','얼굴이 처','갑자기 마비']},
    {code:'SEIZURE',level:'AI-T2',terms:['경련','발작']},
    {code:'TRAUMA',level:'AI-T2',terms:['추락','교통사고','큰 사고','머리를 세게']},
    {code:'POISONING',level:'AI-T2',terms:['과다복용','세제 마셨','독극물','약을 20알','수면제']},
    {code:'ANAPHYLAXIS',level:'AI-T1',terms:['전신 알레르기','목이 붓','입술이 붓']}
  ],
  promptAttackTerms:['시스템 프롬프트','이전 지시를 무시','규칙 무시','프롬프트 보여'],
  medicationDoseTerms:['몇 알','몇 mg','용량','두 배 먹','복용량','약 처방'],
  diagnosisTerms:['무슨 병','진단 확정','확실히 진단','병명이 뭐'],
  nonMedicalTerms:['비트코인','주식','코딩','정치','종교','쇼핑','연예'],
  intro:'안녕하세요. 현재 증상을 짧게 정리하고 필요한 정보만 확인하겠습니다. 위험 신호가 있으면 설명보다 긴급 행동과 응급실 연결을 우선합니다.'
};