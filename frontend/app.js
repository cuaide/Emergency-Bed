
(() => {
  'use strict';
  const DATA = window.APP_DATA;
  const API = window.APP_API;
  const PROMPT = window.AI_PROMPT_ENV || {redFlags:[],maxQuestions:4,intro:'안녕하세요. 현재 상태를 정리해드릴게요.'};
  const app = document.getElementById('app');
  const toastStack = document.getElementById('toast-stack');
  const modalRoot = document.getElementById('modal-root');

  const guidesHtml = '';

  const actionGuides={
    consciousness:{title:'의식저하',emoji:'😵',steps:['의식 상태 확인','기도 확보','옆으로 누운 자세 유지','호흡 확인','119 신고'],image:'./public/의식 저하.png'},
    seizure:{title:'경련',emoji:'⚡',steps:['주변의 위험한 물건 치우고','바닥에 누워 몸을 옆으로 기울여','입에 놓지 않기 (혀 깨물림 방지)','지속 시간 확인 및 119 신고','경련 후 상태 관찰 및 의료진 평가'],image:'./public/경련.png'},
    severe_bleeding:{title:'심한 출혈',emoji:'🩸',steps:['출혈부위 확인','깨끗한 천으로 압박','지혈대 위 확인','다리를 올린상태 유지','119 신고'],image:'./public/심한 출혈.png'},
    burn:{title:'화상',emoji:'🔥',steps:['냉수로 15~20분 식히기','옷 제거하기','멸균 거즈로 덮기','진통제 복용 검토','119 신고'],image:'./public/화상.png'}
  };

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k,v) { try { localStorage.setItem(k,v); } catch {} },
    remove(k) { try { localStorage.removeItem(k); } catch {} }
  };

  function savedLocation(){
    try {
      const v = JSON.parse(store.get('er_location') || 'null');
      return v && Number.isFinite(v.lat) && Number.isFinite(v.lng) ? v : null;
    } catch { return null; }
  }
  function rememberLocation(loc){
    if(!loc || !Number.isFinite(loc.lat) || !Number.isFinite(loc.lng)) return;
    store.set('er_location', JSON.stringify(loc));
    if(window.APP_API && window.APP_API.setSearchCenter) window.APP_API.setSearchCenter(loc.lat, loc.lng);
  }

  const state = {
    memberType: ['free','subscriber','member'].includes(store.get('er_member_type')) ? 'member' : 'guest',
    user: store.get('er_user') || '',
    consents: {
      location: store.get('er_consent_location') === '1',
      health: store.get('er_consent_health') === '1',
      record: store.get('er_consent_record') === '1',
      aiQuality: store.get('er_consent_ai_quality') === '1'
    },

    location: savedLocation() || { ...DATA.defaultLocation },
    locationConfirmed: !!savedLocation(),
    search: { symptoms:[], age:'', conscious:'', breathing:'', memo:'', transcript:'' },
    results: [], activeResult:'', selected:'', radiusKm:5, radiusExpanded:false, refreshedAt:new Date(), highRisk:false,
    severity:'auto', intake:null, recommendationMeta:null,
    required: { bed:'er', bedLabel:'응급실 병상', equipment:[], unsupported:[] },
    chatAnswers: {}, refreshCount:0, selectedAmbulance:'',
    recentHospitals: (()=>{try{const list=JSON.parse(store.get('er_recent_hospitals')||'[]');if(Array.isArray(list)&&list.length)return list.slice(0,5);const legacy=JSON.parse(store.get('er_recent_hospital')||'null');return legacy?[legacy]:[];}catch{return []}})(),
    profiles: (()=>{try{const list=JSON.parse(store.get('er_family_profiles')||'[]');if(Array.isArray(list)&&list.length)return list;}catch{}return [{id:'self',relation:'본인',name:'내 프로필',age:'',bloodType:'',allergies:'',medications:'',conditions:'',note:''},{id:'family-mother',relation:'어머니',name:'어머니',age:'68',bloodType:'',allergies:'',medications:'',conditions:'',note:''}];})(),
    selectedProfileId: store.get('er_selected_profile')||'manual'
  };
  let activeRecognition=null;
  let activeRecognitionTimer=null;
  let activeRecognitionBase='';
  let chatRecognition=null;
  let chatRecognitionTimer=null;

  function esc(v='') { return String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function go(path) { location.hash = path.startsWith('#') ? path : `#${path}`; }
  function formatTime(d) { return d.toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'}); }
  function memberLabel() { return state.memberType==='member'?'회원':'비회원'; }
  function memberClass() { return state.memberType==='member'?'member':'guest'; }

  function icon(name, size=22, stroke='currentColor') {
    const p = {
      home:'<path d="M3 11.5 12 4l9 7.5v8a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
      pin:'<path d="M12 21s6-5.3 6-11a6 6 0 1 0-12 0c0 5.7 6 11 6 11Z"/><circle cx="12" cy="10" r="2"/>',
      chat:'<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v7a2.5 2.5 0 0 1-2.5 2.5H10l-5 4v-4.7A2.5 2.5 0 0 1 4 12.5z"/><path d="M8 8h8M8 11h5"/>',
      back:'<path d="m15 18-6-6 6-6"/>', close:'<path d="m7 7 10 10M17 7 7 17"/>', arrow:'<path d="M5 12h14M14 7l5 5-5 5"/>', chevron:'<path d="m9 18 6-6-6-6"/>',
      location:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2"/>',
      refresh:'<path d="M20 6v5h-5M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9M5.5 15A7 7 0 0 0 18 17.5l2-2.5"/>',
      clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', check:'<circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/>',
      heart:'<path d="M20.5 9.5c0 5-8.5 10-8.5 10s-8.5-5-8.5-10A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 8.5 2.5Z"/>',
      medical:'<path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/>', people:'<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20a6 6 0 0 1 12 0M14 15a5 5 0 0 1 7 5"/>',
      crown:'<path d="m4 8 4 3 4-6 4 6 4-3-2 10H6z"/>', map:'<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/>',
      copy:'<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
      car:'<path d="m5 16-1.5-3 2-6h13l2 6L19 16"/><path d="M5 16v3M19 16v3M4 13h16M8 10h8"/>',
      phone:'<path d="M7 3h3l1.5 4-2 1.5a14 14 0 0 0 6 6l1.5-2 4 1.5v3c0 1.1-.9 2-2 2C10.7 19 5 13.3 5 5c0-1.1.9-2 2-2Z"/>',
      ambulance:'<path d="M3 7h12v11H3zM15 10h4l2 3v5h-6z"/><circle cx="7" cy="19" r="2"/><circle cx="18" cy="19" r="2"/><path d="M7 10h4M9 8v4"/>',
      taxi:'<path d="m4 16 2-6h12l2 6v3H4z"/><path d="M9 10V7h6v3"/><circle cx="8" cy="19" r="1.5"/><circle cx="16" cy="19" r="1.5"/>',
      user:'<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
      bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
      info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
      mic:'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/>', stop:'<rect x="7" y="7" width="10" height="10" rx="1.5"/>', send:'<path d="m3 11 18-8-8 18-2-8zM11 13 21 3"/>',
      lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
      alert:'<path d="M12 3 2.5 20h19z"/><path d="M12 9v5M12 17h.01"/>', shield:'<path d="M12 3 4 6v5c0 5 3.4 8.3 8 10 4.6-1.7 8-5 8-10V6z"/><path d="m8.5 12 2.3 2.3 4.7-5"/>',
      edit:'<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>', star:'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
      rain:'<path d="M7 15a4 4 0 0 1 .3-7.97 5 5 0 0 1 9.4-1.98A4.5 4.5 0 0 1 17 15H7Z"/><path d="M9 18.5V20M12 18.5V21M15 18.5V20"/>',cloud:'<path d="M7 18a4.5 4.5 0 0 1 .3-8.97 5.5 5.5 0 0 1 10.4-1.98A4.75 4.75 0 0 1 17.5 18H7Z"/>',
      snow:'<path d="M7 15a4 4 0 0 1 .3-7.97 5 5 0 0 1 9.4-1.98A4.5 4.5 0 0 1 17 15H7Z"/><path d="M9 19h.01M12 19h.01M15 19h.01M9 21h.01M15 21h.01"/>',
      clear:'<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>'
    };
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${stroke}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p[name]||p.info}</svg>`;
  }

  function header(title, subtitle='', {back=true, close=false, home=false, emergency=false}={}) {
    if(home) return `<div class="status-spacer"></div><header class="topbar home-bar refined-home-bar"><div class="top-title" style="text-align:left"><span class="header-brand"><img src="./public/guide-1.png" alt="응급 나침반 로고"><span><strong>응급 나침반</strong><small>빠르고 정확한 응급실 길잡이</small></span></span></div><div class="home-header-actions"><button class="icon-btn transparent notification-btn" data-notifications aria-label="알림">${icon('bell',20)}<i>2</i></button><button class="icon-btn transparent" data-account aria-label="내 정보">${icon('user',21)}</button></div></header>`;
    return `<div class="status-spacer"></div><header class="topbar"><button class="icon-btn transparent" data-back aria-label="뒤로가기">${back?icon('back',22):''}</button><div class="top-title"><strong>${esc(title)}</strong>${subtitle?`<small>${esc(subtitle)}</small>`:''}</div>${emergency?`<button class="header-119" data-go-119 aria-label="119 긴급 연락">${icon('phone',14,'#fff')} 119</button>`:`<button class="icon-btn transparent" ${close?'data-close-report':'aria-hidden="true"'} aria-label="닫기">${close?icon('close',21):''}</button>`}</header>`;
  }
  function tabs(active='home') {
    const list=[['home','홈','/home','home'],['find','응급실 찾기','/search','pin'],['chat','AI진단','/ai-access','chat']];
    return `<nav class="bottom-tabs">${list.map(([id,label,path,ico])=>`<button class="tab-btn ${active===id?'active':''}" data-route="${path}">${icon(ico,21)}<span>${label}</span></button>`).join('')}</nav>`;
  }
  function shell(content,{title='',subtitle='',tab='home',back=true,close=false,noTabs=false,home=false,action=''}={}) {
    return `<section class="app-screen">${header(title,subtitle,{back,close,home})}<div class="scroll-area ${noTabs?'no-tabs':''} ${action?'has-action':''}">${content}</div>${action}${noTabs?'':tabs(tab)}</section>`;
  }
  function toast(msg) {
    const item=document.createElement('div');
    item.className='toast-item';
    item.innerHTML=`<div class="toast-message">${esc(msg)}</div><button class="toast-close" aria-label="토스트 닫기">${icon('close',16,'#fff')}</button>`;
    toastStack.appendChild(item);
    requestAnimationFrame(()=>item.classList.add('show'));
    const remove=()=>{ item.classList.remove('show'); setTimeout(()=>item.remove(),220); };
    item.querySelector('.toast-close').onclick=remove;
    setTimeout(remove,2000);
  }
  function closeModal() { modalRoot.innerHTML=''; }
  function modal({title,body,primary='확인',secondary='닫기',onPrimary,hideSecondary=false,onSecondary}) {
    modalRoot.innerHTML=`<div class="modal"><section class="sheet" role="dialog" aria-modal="true"><div class="handle"></div><h2>${esc(title)}</h2><div>${body}</div><div class="sheet-actions"><button class="btn btn-primary btn-block" data-sheet-ok>${esc(primary)}</button>${hideSecondary?'':`<button class="btn btn-secondary btn-block" data-sheet-close>${esc(secondary)}</button>`}</div></section></div>`;
    modalRoot.querySelector('[data-sheet-ok]').onclick=()=>{ closeModal(); if(onPrimary) onPrimary(); };
    modalRoot.querySelector('[data-sheet-close]')?.addEventListener('click',()=>{ closeModal(); if(onSecondary) onSecondary(); });
    modalRoot.querySelector('.modal').onclick=e=>{ if(e.target.classList.contains('modal')) closeModal(); };
  }

  function detectHighRisk() {
    const all=[...state.search.symptoms,state.search.conscious,state.search.breathing,state.search.memo,state.search.transcript,profileSearchText()].join(' ');
    state.highRisk=/호흡 곤란|숨쉬기 어려움|의식|반응이 느림|대량.?출혈|경련|마비|쓰러|숨을 못|의식 없음/.test(all);
    const el=document.querySelector('[data-risk-banner]'); if(el) el.classList.toggle('show',state.highRisk);
    return state.highRisk;
  }

  /** 증상·메모·프로필을 한 문장으로 합친다. 백엔드 입력 구조화(1단계)에 그대로 넘긴다. */
  function symptomText(){
    return [...state.search.symptoms,state.search.memo,state.search.transcript,state.search.conscious,state.search.breathing,profileSearchText()].filter(Boolean).join(' ');
  }

  function mapRequirement() {
    const text=symptomText();
    // profile 은 백엔드 추천 엔진의 요구자원 프로파일 키다 (/api/v1/recommendations/profiles).
    // 이 값이 없으면 서버가 'general'(경증)로 채점해서 화상·외상 전문병원이 안 올라온다.
    let bed='er', label='응급실 병상', equipment=[], profile='general';
    if(/흉통|가슴/.test(text)){ bed='internal'; label='내과 중환자실'; equipment=['angiography']; profile='cardiac'; }
    if(/마비|의식|두통/.test(text)){ bed='neurosurgery'; label='신경외과 중환자실'; equipment=['CT']; profile='neuro'; }
    if(/외상|출혈|절단/.test(text)){ bed='trauma'; label='외상 중환자실'; equipment=['operating']; profile='trauma'; }
    if(/복통|복부/.test(text)){ bed='surgical'; label='외과 중환자실'; equipment=['operating']; profile='abdominal_surgery'; }
    if(/화상/.test(text)){ bed='burn'; label='화상 중환자실'; profile='burn'; }
    if(/중독|약물|독극물/.test(text)){ bed='poison'; label='약물중독 중환자실'; profile='poisoning'; }
    if(/호흡/.test(text)){ bed='general'; label='일반 중환자실'; equipment=['ventilator']; profile='unspecified_icu'; }
    state.required={bed,bedLabel:label,equipment,profile,unsupported:equipment.filter(e=>e!=='operating')};
    return state.required;
  }


  function structureSearchInput(){
    mapRequirement();
    detectHighRisk();
    // 화면이 고위험 신호를 잡았으면 중증으로 요청하고, 아니면 서버 자동 판정에 맡긴다.
    state.severity=state.highRisk?'severe':'auto';
    return state.required;
  }


  function agoText(m){ return m>=999?'갱신 시각 미확인':`${m}분 전`; }


  async function loadRecommendations({detail=false}={}) {
    structureSearchInput();

    const payload=await API.fetchRecommendations({
      detail,
      profile:state.required.profile||null,
      symptom:symptomText()||null,
      severity:state.severity||'auto'
    });
    applyRecommendations(payload);
    const missingRouteIds=state.results
      .filter(h=>!Array.isArray(h.raw?.route?.path)||h.raw.route.path.length<2)
      .map(h=>h.id);
    if(missingRouteIds.length){
      const routed=await API.fetchRoutesFor(missingRouteIds);
      const byId=new Map((routed||[]).map(row=>[String(row.hpid),row.route]));
      state.results.forEach(h=>{
        const route=byId.get(String(h.id));
        if(!route||!Array.isArray(route.path)||route.path.length<2)return;
        h.raw.route=route;
        if(Number.isFinite(Number(route.distance_km)))h.distanceKm=Number(route.distance_km);
        if(Number.isFinite(Number(route.duration_min)))h.eta=Math.max(1,Math.round(Number(route.duration_min)));
        h.traffic=window.APP_ADAPTER.trafficLabel(route);
        h.congestionLabel=route.congestion_label||route.traffic_status||null;
      });
    }
    return payload;
  }
  function applyRecommendations(payload){
    const A=window.APP_ADAPTER;
    const list=(payload && payload.recommendations) || [];
    state.results=list.map(item=>A.normalizeRecommendation(item));
    state.recommendationMeta={
      ruleVersion:payload?.rule_version||'', profile:payload?.profile||null,
      radiusKm:payload?.radius_km??null, evaluated:payload?.evaluated_count??null,
      excluded:payload?.excluded_count??null, notes:payload?.notes||[],
      resourceHolders:payload?.resource_holders||[],
      mode:A.normalizeMode(payload), pipeline:A.normalizePipeline(payload),
      radiusSteps:payload?.radius_steps||[],
      hasBest:payload?.has_best===true, bestId:payload?.best_hpid||null,
      input:payload?.input||null,
      disclaimer:payload?.disclaimer||''
    };
    if(state.recommendationMeta.mode) state.severity=state.recommendationMeta.mode.key;
    if(payload?.weather?.current){
      const w=A.normalizeWeather(payload.weather);
      if(w) DATA.currentWeather=w;
    }
    state.radiusKm=payload?.radius_km??state.radiusKm;
    state.radiusExpanded=(payload?.radius_km??0)>5;
    state.activeResult=state.results[0]?.id||''; state.selected=state.activeResult; state.refreshedAt=new Date();
    API.saveAuditLog({timestamp:new Date().toISOString(),memberType:state.memberType,requiredBed:state.required.bed,radiusKm:state.radiusKm,mode:state.severity,results:state.results.map(h=>({id:h.id,score:h.total,grade:h.grade,updatedMinutes:h.updatedMinutes})),algorithmVersion:payload?.rule_version||'backend'});
  }

  function homeStats() {
    const within=DATA.hospitals.filter(h=>Number(h.distanceKm)<=5);
    const fastest=within.length?Math.min(...within.map(h=>Number(h.eta)||999)):null;
    return {
      within: within.length,
      fresh: within.filter(h=>Number(h.updatedMinutes)<=30).length,
      fastest,
      available: within.filter(h=>Number(h.beds?.er)>0).length
    };
  }
  function saveRecentHospital(h) {
    if(!h) return;
    const entry={id:h.id,name:h.name,address:h.address,checkedAt:new Date().toISOString()};
    state.recentHospitals=[entry,...state.recentHospitals.filter(x=>x.id!==h.id)].slice(0,5);
    store.set('er_recent_hospitals',JSON.stringify(state.recentHospitals));
    store.set('er_recent_hospital',JSON.stringify(entry));
  }
  function persistProfiles(){store.set('er_family_profiles',JSON.stringify(state.profiles));}
  function currentProfile(){return state.profiles.find(p=>p.id===state.selectedProfileId)||null;}
  function ageGroup(age){const n=Number(age);if(!n)return '';if(n<=12)return '0~12세';if(n<=18)return '13~18세';if(n<=64)return '19~64세';return '65세 이상';}
  function profileSearchText(){const p=currentProfile();if(!p)return '';return [p.relation,p.name,p.age?`${p.age}세`:'',p.conditions,p.allergies,p.medications,p.note].filter(Boolean).join(' ');}
  function applyProfileToSearch(id){state.selectedProfileId=id;store.set('er_selected_profile',id);if(id==='manual'){state.search.age='';return;}const p=currentProfile();if(p?.age)state.search.age=ageGroup(p.age);}
  function enableHorizontalDrag(el){if(!el)return;let down=false,startX=0,startScroll=0,moved=false,pid=null;el.addEventListener('pointerdown',e=>{if(e.button!==0)return;down=true;pid=e.pointerId;startX=e.clientX;startScroll=el.scrollLeft;moved=false;try{el.setPointerCapture(pid);}catch{}});el.addEventListener('pointermove',e=>{if(!down)return;const dx=e.clientX-startX;if(Math.abs(dx)>5){moved=true;el.classList.add('dragging');el.scrollLeft=startScroll-dx;e.preventDefault();}});const end=e=>{if(!down)return;down=false;el.classList.remove('dragging');if(moved){el.dataset.dragged='1';setTimeout(()=>delete el.dataset.dragged,0);}try{if(pid!=null)el.releasePointerCapture?.(pid);}catch{}};el.addEventListener('pointerup',end);el.addEventListener('pointercancel',end);}
  function openProfileEditor(profile=null){
    const editing=!!profile;const p=profile||{id:`profile-${Date.now()}`,relation:'가족',name:'',age:'',bloodType:'',allergies:'',medications:'',conditions:'',note:''};
    modalRoot.innerHTML=`<div class="modal"><section class="sheet profile-editor-sheet" role="dialog" aria-modal="true"><div class="handle"></div><h2>${editing?'프로필 수정':'가족 프로필 추가'}</h2><p>응급실 찾기에 활용할 최소 정보를 입력하세요. 입력하지 않은 항목은 검색 조건에서 제외됩니다.</p><div class="profile-form-grid"><label><span>관계</span><select class="field" id="profile-relation"><option>본인</option><option>배우자</option><option>자녀</option><option>어머니</option><option>아버지</option><option>가족</option><option>기타</option></select></label><label><span>이름/별칭</span><input class="field" id="profile-name" maxlength="20" value="${esc(p.name)}" placeholder="예: 어머니"></label><label><span>나이</span><input class="field" id="profile-age" type="number" min="0" max="120" value="${esc(p.age)}" placeholder="선택"></label><label><span>혈액형</span><select class="field" id="profile-blood"><option value="">선택 안 함</option><option>A</option><option>B</option><option>AB</option><option>O</option><option>모름</option></select></label><label class="full"><span>알레르기</span><input class="field" id="profile-allergy" maxlength="80" value="${esc(p.allergies)}" placeholder="예: 페니실린"></label><label class="full"><span>기저질환</span><input class="field" id="profile-condition" maxlength="100" value="${esc(p.conditions)}" placeholder="예: 고혈압, 당뇨"></label><label class="full"><span>복용약</span><input class="field" id="profile-medication" maxlength="100" value="${esc(p.medications)}" placeholder="예: 혈압약"></label><label class="full"><span>기타 메모</span><textarea class="field" id="profile-note" maxlength="120" placeholder="의료진에게 전달할 참고사항">${esc(p.note)}</textarea></label></div><div class="sheet-actions"><button class="btn btn-primary btn-block" data-profile-save>${editing?'저장':'프로필 추가'}</button>${editing&&p.id!=='self'?'<button class="btn btn-danger-soft btn-block" data-profile-delete>프로필 삭제</button>':''}<button class="btn btn-secondary btn-block" data-sheet-close>취소</button></div></section></div>`;
    document.getElementById('profile-relation').value=p.relation||'가족';document.getElementById('profile-blood').value=p.bloodType||'';
    modalRoot.querySelector('[data-profile-save]').onclick=()=>{const item={...p,relation:document.getElementById('profile-relation').value,name:document.getElementById('profile-name').value.trim()||document.getElementById('profile-relation').value,age:document.getElementById('profile-age').value,bloodType:document.getElementById('profile-blood').value,allergies:document.getElementById('profile-allergy').value.trim(),conditions:document.getElementById('profile-condition').value.trim(),medications:document.getElementById('profile-medication').value.trim(),note:document.getElementById('profile-note').value.trim()};const idx=state.profiles.findIndex(x=>x.id===item.id);if(idx>=0)state.profiles[idx]=item;else state.profiles.push(item);persistProfiles();closeModal();toast('가족 프로필을 저장했습니다.');renderProfiles();bindCommon();};
    modalRoot.querySelector('[data-profile-delete]')?.addEventListener('click',()=>{state.profiles=state.profiles.filter(x=>x.id!==p.id);if(state.selectedProfileId===p.id)applyProfileToSearch('manual');persistProfiles();closeModal();toast('프로필을 삭제했습니다.');renderProfiles();bindCommon();});
    modalRoot.querySelector('[data-sheet-close]').onclick=closeModal;
  }
  function relativeRecentLabel(iso){
    if(!iso) return '최근 검색';
    const d=new Date(iso); const now=new Date(); const diff=Math.max(0,Math.round((now-d)/60000));
    if(diff<1)return '방금 검색'; if(diff<60)return `${diff}분 전 검색`; if(diff<1440)return `${Math.floor(diff/60)}시간 전 검색`; return `${Math.floor(diff/1440)}일 전 검색`;
  }

  function renderLogin() {
    const autoChecked=store.get('er_auto_login')==='1';
    app.innerHTML=`<section class="app-screen"><div class="login-bg login-bg-v4"><div class="login-brand-v4"><img src="./public/guide-2.png" alt="응급 나침반"><p>가까운 곳보다 <strong>지금 더 적합한 응급실</strong>을 찾습니다.</p></div><div class="login-visual-v4"><div class="login-visual-copy"><span class="login-kicker">Emergency Compass</span><h1>생명을 지키는<br>빠른 길찾기</h1><p>위치·병상·도착 시간을 함께 비교해요.</p></div><img src="./public/guide-3.png" alt="응급 나침반 캐릭터"></div><div class="login-card login-card-v4"><div class="field-label">회원 로그인</div><input class="field" id="login-id" value="${esc(state.user)}" placeholder="이메일 또는 휴대폰 번호" autocomplete="username"><input class="field" id="login-pw" type="password" maxlength="16" placeholder="비밀번호" autocomplete="current-password"><label class="auto-login-row"><input type="checkbox" id="auto-login" ${autoChecked?'checked':''}><span>자동 로그인</span></label><div class="login-actions"><button class="btn btn-primary btn-block" data-login>회원 로그인</button><button class="btn btn-secondary btn-block" data-guest>비회원으로 이용</button></div><div class="login-secondary-links"><button class="policy-link" data-route="/signup">회원가입</button><span class="divider" aria-hidden="true"></span><button class="policy-link" data-route="/policy">데이터 처리 안내</button></div></div><p class="safe-note">응급실 찾기는 로그인 없이 이용할 수 있으며, AI진단은 회원 기능입니다.</p></div></section>`;
    app.querySelector('[data-login]').onclick=()=>{const id=document.getElementById('login-id').value.trim(),pw=document.getElementById('login-pw').value,auto=document.getElementById('auto-login').checked;if(!id||!pw)return toast('아이디와 비밀번호를 입력해 주세요.');state.memberType='member';state.user=id;store.set('er_member_type','member');store.set('er_user',id);auto?store.set('er_auto_login','1'):store.remove('er_auto_login');go('/home');};
    app.querySelector('[data-guest]').onclick=()=>{state.memberType='guest';state.user='';store.set('er_member_type','guest');store.remove('er_auto_login');go('/home');};
  }

  function renderSignup() {
    const termRows=[
      ['terms','[필수] 서비스 이용약관','서비스 이용 및 회원계약에 관한 조건입니다.','서비스 이용약관','서비스 목적, 회원의 권리와 의무, 서비스 제한, 책임 범위, 탈퇴 및 분쟁 처리 기준을 안내합니다.'],
      ['privacy','[필수] 개인정보 처리 안내','계정 생성과 인증에 필요한 최소 정보를 처리합니다.','개인정보 처리 안내','이메일 또는 휴대폰 번호, 비밀번호 해시, 회원 ID와 접속 기록을 계정 운영 목적으로 처리합니다.'],
      ['age14','[필수] 만 14세 이상 확인','AI진단 회원 기능은 만 14세 이상을 기준으로 합니다.','연령 확인 안내','만 14세 미만 회원은 법정대리인 동의 절차가 필요합니다. 비회원 응급실 찾기는 연령과 관계없이 이용할 수 있습니다.'],
      ['marketing','[선택] 혜택 및 마케팅 정보 수신','거부해도 기본 서비스 이용에 영향이 없습니다.','마케팅 정보 수신 안내','서비스 업데이트, 구독 혜택 및 이벤트 정보를 선택한 채널로 받을 수 있습니다. 언제든 설정에서 철회할 수 있습니다.']
    ];
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">회원가입</p><h1 class="page-title">응급 나침반 회원가입</h1><p class="page-subtitle">AI진단을 이용할 회원 정보를 입력해 주세요.</p></div><div class="card"><label><span class="field-label">이메일 또는 휴대폰</span><input class="field" id="signup-id" placeholder="example@email.com"></label><label style="display:block;margin-top:10px"><span class="field-label">비밀번호</span><div class="password-wrap"><input class="field" id="signup-pw" type="password" maxlength="16" placeholder="8~16자 문자·숫자·기호"><button type="button" class="password-toggle" data-password-toggle aria-label="비밀번호 보기">보기</button></div><p class="validation-message" id="pw-validation"></p></label></div><div class="section-head"><h2 class="section-title">약관 및 확인</h2></div><div class="consent-card">${termRows.map(([id,title,desc,modalTitle,detail])=>`<div class="consent-row term-row"><label><input type="checkbox" id="${id}"><span><strong>${title}</strong><span>${desc}</span></span></label><button class="term-detail-btn" data-term-title="${modalTitle}" data-term-detail="${detail}" aria-label="${modalTitle} 자세히 보기">${icon('chevron',18)}</button></div>`).join('')}</div><div style="margin-top:14px"><button class="btn btn-primary btn-block" data-signup>회원가입</button></div></div>`,{title:'회원가입',subtitle:'회원 기능',noTabs:true});
    const pw=document.getElementById('signup-pw'),v=document.getElementById('pw-validation');
    const validate=()=>{const n=pw.value.length;v.textContent=n>0&&n<8?'비밀번호는 8자 이상 입력해 주세요.':'';v.classList.toggle('show',!!v.textContent);return n>=8&&n<=16;};
    pw.addEventListener('input',validate);
    app.querySelector('[data-password-toggle]').onclick=e=>{pw.type=pw.type==='password'?'text':'password';e.currentTarget.textContent=pw.type==='password'?'보기':'숨김';};
    app.querySelectorAll('[data-term-title]').forEach(b=>b.onclick=()=>modal({title:b.dataset.termTitle,body:`<div class="terms-detail"><p>${esc(b.dataset.termDetail)}</p><h3>주요 내용</h3><ul><li>서비스 목적과 제공 범위</li><li>회원의 권리·의무 및 이용 제한</li><li>정보 처리 목적과 보유 기준</li><li>동의 철회 및 문의 방법</li></ul></div>`,primary:'확인',hideSecondary:true}));
    app.querySelector('[data-signup]').onclick=()=>{const id=document.getElementById('signup-id').value.trim();if(!id)return toast('이메일 또는 휴대폰 번호를 입력해 주세요.');if(!validate())return toast('비밀번호 입력 조건을 확인해 주세요.');if(!document.getElementById('terms').checked||!document.getElementById('privacy').checked||!document.getElementById('age14').checked)return toast('필수 약관과 연령 확인에 동의해 주세요.');state.memberType='member';state.user=id;store.set('er_member_type','member');store.set('er_user',id);toast('회원가입이 완료되었습니다.');setTimeout(()=>go('/home'),350);};
  }

  function renderHome() {
    const memberLocked=state.memberType!=='member';
    const stats=homeStats();
    const recent=state.recentHospitals||[];
    const guides=DATA.emergencyGuides||[];
    const profiles=state.profiles||[];
    const recentRows=recent.length?recent.map(r=>`<article class="recent-card"><span class="recent-icon">${icon('clock',20)}</span><div class="recent-copy"><strong>${esc(r.name)}</strong><span>${relativeRecentLabel(r.checkedAt)}</span></div><button class="mini-btn" data-recent-open="${esc(r.id)}">다시 확인 ${icon('chevron',14)}</button></article>`).join(''):`<article class="recent-card empty"><span class="recent-icon">${icon('clock',20)}</span><div class="recent-copy"><strong>최근 선택한 응급실이 없습니다</strong><span>병원을 선택하면 최대 5개까지 기록됩니다.</span></div></article>`;
    const guideButtonsHtml=`<section style="margin-top:18px"><div style="margin-bottom:12px"><h2 style="font-size:17px;font-weight:750;margin:0">응급 상황 행동 가이드</h2></div><div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px">${Object.entries(actionGuides).map(([key,g])=>`<button class="action-guide-btn" data-action-guide="${key}" style="border:1px solid var(--line);border-radius:12px;padding:12px;background:#fff;text-align:center;cursor:pointer"><small style="display:block;font-size:13px;font-weight:700;color:var(--ink)">${g.title}</small><small style="display:block;font-size:10px;color:var(--muted);margin-top:4px">대처법 보기</small></button>`).join('')}</div></section>`;
    const memberPanel=state.memberType==='member'?`<section class="family-home-section"><div class="home-section-head"><div><h2>가족 응급 프로필</h2><p>본인·가족 정보를 미리 등록</p></div><button class="text-btn" data-route="/profiles">${profiles.length}개 관리</button></div><section class="family-summary-card"><span class="round-icon">${icon('people',21)}</span><div><strong>사고자 정보를 빠르게 불러오세요</strong><p>나이, 알레르기, 기저질환, 복용약을 응급실 찾기에 바로 적용할 수 있습니다.</p></div><button class="mini-btn" data-route="/profiles">관리</button></section></section><section class="home-member-section"><div class="home-section-head"><div><h2>최근 이용</h2><p>최근 선택 병원 ${recent.length}/5</p></div></div><div class="recent-history-scroll ${recent.length>2?'scrollable':''}">${recentRows}</div></section>`:'';
    app.innerHTML=shell(`<div class="page compact home-page-v4"><section class="home-location-card"><span class="home-location-icon">${icon('pin',21)}</span><div><small>현재 위치</small><strong>${esc(state.locationConfirmed?state.location.address:'위치를 설정해 주세요')}</strong><span>${state.locationConfirmed?`위치 정확도 ${esc(state.location.accuracy)} · 위치 변경`:'GPS 또는 주소 입력으로 검색 기준을 설정합니다.'}</span></div><button class="location-change-btn" data-location>변경 ${icon('chevron',15)}</button></section>${DATA.currentWeather?.rainRisk?`<div class="notice" style="margin:10px 0 0">${icon('rain',16)}<span>비 소식이 있어요. 외출 전 우산을 챙기세요.</span></div>`:''}<section class="home-main-card"><div class="home-main-copy"><span class="home-main-eyebrow">가장 중요한 기능</span><h1>지금 갈 수 있는<br>응급실 찾기</h1><p>현재 위치와 환자 상태에 맞는 응급실을 빠르게 찾아드려요.</p><button class="home-main-button" data-route="/search">응급실 찾기 ${icon('arrow',17)}</button></div><img src="./public/guide-3.png" alt="응급 나침반 캐릭터"></section><section class="home-status-card"><div class="home-section-head"><div><h2>내 주변 응급실 현황</h2><p>실시간 병상·경로 데이터 요약</p></div><button class="home-refresh-btn" data-home-refresh>${icon('refresh',16)} <span>갱신</span></button></div><div class="home-status-grid"><div><span>${icon('medical',18)}</span><small>5km 내 응급실</small><strong>${stats.within}<i>곳</i></strong></div><div><span>${icon('check',18)}</span><small>최신 정보 확인</small><strong>${stats.fresh}<i>곳</i></strong></div><div><span>${icon('clock',18)}</span><small>가장 빠른 예상시간</small><strong>${stats.fastest??'-'}<i>분</i></strong></div><div><span>${icon('people',18)}</span><small>병상 정보 있음</small><strong>${stats.available}<i>곳</i></strong></div></div><div class="home-status-foot"><span>${formatTime(state.refreshedAt)} 갱신</span><span>병상 · 이동시간 · 데이터 갱신시각 반영</span></div></section><section class="home-ai-card ${memberLocked?'locked':''}"><div><span class="home-ai-label">회원 전용</span><h2>어떤 증상인지 설명하기 어렵나요?</h2><p>AI진단이 현재 상태를 정리하고 응급실 검색 조건으로 연결해드려요.</p><button class="btn btn-primary" data-ai-home>${memberLocked?'AI진단 안내':'AI진단 시작'} ${icon('arrow',16,'#fff')}</button></div><span class="home-ai-visual">${icon(memberLocked?'lock':'chat',34)}</span></section>${guideButtonsHtml}${memberPanel}${guidesHtml}</div>`,{home:true,tab:'home'});
    app.querySelector('[data-location]').onclick=()=>requestLocationConsent();
    app.querySelector('[data-home-refresh]').onclick=async e=>{const b=e.currentTarget;b.classList.add('loading');b.disabled=true;toast('주변 응급실 데이터를 갱신하고 있어요.');try{const rows=await API.fetchHospitals();if(Array.isArray(rows)&&rows.length)DATA.hospitals=rows;state.refreshedAt=new Date();setTimeout(()=>{renderHome();bindCommon();toast('주변 응급실 현황을 갱신했습니다.');},420);}catch{b.disabled=false;b.classList.remove('loading');toast('데이터를 갱신하지 못했습니다. 잠시 후 다시 시도해 주세요.');}};
    app.querySelector('[data-ai-home]').onclick=()=>{if(memberLocked){modal({title:'AI진단은 회원 기능입니다',body:`<p>응급실 찾기는 로그인 없이 바로 이용할 수 있습니다. AI진단을 사용하려면 회원 로그인을 진행해 주세요.</p><div class="notice">${icon('info',15)}<span>응급 상황이라면 로그인보다 응급실 찾기를 먼저 이용하세요.</span></div>`,primary:'회원 로그인',secondary:'응급실 찾기',onPrimary:()=>go('/login'),onSecondary:()=>go('/search')});}else go('/ai-access');};
    const guideSlider=app.querySelector('.home-guide-slider');app.querySelectorAll('[data-guide]').forEach(btn=>btn.addEventListener('click',e=>{if(guideSlider?.dataset.dragged==='1')return;const idx=Number(btn.dataset.guide);const g=guides[idx];if(g)showGuideImageModal(g);}));
    app.querySelectorAll('[data-recent-open]').forEach(b=>b.onclick=()=>{const id=b.dataset.recentOpen;const found=DATA.hospitals.find(h=>h.id===id);if(!found)return toast('최근 병원 정보를 다시 불러올 수 없습니다.');const result=state.results.find(h=>h.id===id);go(result?`/report/${id}`:'/results');});
    enableHorizontalDrag(guideSlider);
    app.querySelector('[data-notifications]')?.addEventListener('click',()=>modal({title:'알림',body:'<div class="notification-list"><p><strong>응급실 데이터가 갱신되었습니다.</strong><span>병상·이동시간 정보를 다시 확인할 수 있습니다.</span></p><p><strong>가족 프로필을 완성해 주세요.</strong><span>알레르기와 복용약 정보를 미리 등록할 수 있습니다.</span></p></div>',primary:'확인',hideSecondary:true}));
    app.querySelectorAll('[data-action-guide]').forEach(b=>b.onclick=()=>{const key=b.dataset.actionGuide;const g=actionGuides[key];if(g){if(g.image)return showGuideImageModal(g);modal({title:`${g.emoji} ${g.title} 대처법`,body:`<div style="text-align:left"><ol style="padding:0 20px;line-height:1.8"><li>${g.steps[0]}</li><li>${g.steps[1]}</li><li>${g.steps[2]}</li><li>${g.steps[3]}</li><li>${g.steps[4]}</li></ol></div>`,primary:'닫기',hideSecondary:true});}});
  }

  function showGuideImageModal(g){
    if(!g)return;
    modalRoot.innerHTML=`<div class="modal guide-image-modal"><section class="guide-image-sheet" role="dialog" aria-modal="true" aria-label="${esc(g.title)} 응급처치 가이드"><div class="guide-image-head"><strong>${g.emoji} ${esc(g.title)} 응급처치 가이드</strong><button data-guide-close aria-label="닫기">${icon('close',19)}</button></div><div class="guide-image-scroll"><img src="${g.image}" alt="${esc(g.title)} 응급처치 단계 이미지"></div><div class="guide-image-foot"><p>빠른 참고용 가이드입니다. 상태가 심하거나 의식·호흡 이상이 있으면 즉시 119에 연락하세요.</p><button class="btn btn-primary btn-block" style="margin-top:8px" data-guide-close-bottom>확인</button></div></section></div>`;
    modalRoot.querySelector('[data-guide-close]').onclick=closeModal;
    modalRoot.querySelector('[data-guide-close-bottom]').onclick=closeModal;
    modalRoot.querySelector('.guide-image-modal').onclick=e=>{if(e.target.classList.contains('guide-image-modal'))closeModal();};
  }

  function renderProfiles(){
    if(state.memberType!=='member'){modal({title:'회원 전용 기능입니다',body:'<p>가족 프로필은 회원 계정에 저장됩니다. 응급실 찾기는 비회원도 바로 이용할 수 있습니다.</p>',primary:'회원 로그인',secondary:'응급실 찾기',onPrimary:()=>go('/login'),onSecondary:()=>go('/search')});return;}
    const rows=state.profiles.map(p=>`<article class="profile-card"><span class="profile-avatar">${icon(p.relation==='본인'?'user':'people',22)}</span><div><strong>${esc(p.name||p.relation)}</strong><span>${esc(p.relation)}${p.age?` · ${esc(p.age)}세`:''}${p.bloodType?` · ${esc(p.bloodType)}형`:''}</span><small>${esc([p.conditions&&`기저질환 ${p.conditions}`,p.allergies&&`알레르기 ${p.allergies}`,p.medications&&`복용약 ${p.medications}`].filter(Boolean).join(' · ')||'등록된 의료 정보 없음')}</small></div><button class="mini-btn" data-edit-profile="${p.id}">수정</button></article>`).join('');
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">회원 개인화</p><h1 class="page-title">가족 응급 프로필</h1><p class="page-subtitle">사고자를 빠르게 선택하고 등록된 정보를 응급실 검색 조건에 활용합니다.</p></div><div class="profile-list">${rows}</div><button class="btn btn-primary btn-block" style="margin-top:12px" data-add-profile>${icon('people',18,'#fff')} 가족 프로필 추가</button><div class="notice" style="margin-top:12px">${icon('shield',15)}<span>프로필 정보는 사용자가 직접 입력한 참고 정보이며 의료기록이나 진단을 대체하지 않습니다.</span></div></div>`,{title:'가족 프로필',subtitle:`${state.profiles.length}개 등록`,noTabs:true});
    app.querySelector('[data-add-profile]').onclick=()=>openProfileEditor();
    app.querySelectorAll('[data-edit-profile]').forEach(b=>b.onclick=()=>openProfileEditor(state.profiles.find(p=>p.id===b.dataset.editProfile)));
  }

  function renderPolicy() {
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">데이터 처리 안내</p><h1 class="page-title">필요할 때만, 필요한 만큼.</h1><p class="page-subtitle">정책 설계서의 최소수집·시점 분리 원칙을 반영했습니다.</p></div><div class="policy-card"><h3>비회원 검색</h3><p>정밀 위치와 증상은 현재 검색 세션에만 사용하며 기본 저장하지 않습니다. 원본 음성 저장은 기본 OFF입니다.</p></div><div class="policy-card"><h3>위치정보</h3><p>자동 위치 조회 직전에 목적과 대체 수단을 설명합니다. 거부해도 주소·건물명·지도핀 입력으로 검색할 수 있습니다.</p></div><div class="policy-card"><h3>AI 건강정보</h3><p>구독 회원이 AI진단를 처음 사용할 때 건강정보 처리 동의를 별도로 받습니다. 기록 저장과 품질 개선은 선택입니다.</p></div><div class="policy-card"><h3>추천 감사로그</h3><p>정밀 좌표·원문 대신 병원 후보, 점수 요소, 데이터·알고리즘 버전을 중심으로 기록하도록 설계했습니다.</p></div><div class="notice" style="margin-top:12px">${icon('info',16)}<span>출시 전 개인정보·위치정보·의료·전자상거래 법률 검토와 응급의학 전문의 검증이 필요합니다.</span></div></div>`,{title:'데이터 처리 안내',subtitle:'정책 V1.0',noTabs:true});
  }

  function requestLocationConsent(after) {
    const consented=state.consents.location;
    const body=consented
      ? `<p>현재 위치는 브라우저가 알려주는 값이라 PC 에서는 오차가 수 km 까지 벌어질 수 있습니다. 정확한 출발지가 필요하면 주소를 직접 입력하세요.</p>`
      : `<p>주변 응급실까지의 실제 이동 경로와 교통 ETA를 계산하기 위해 현재 위치를 사용합니다.</p><div class="notice">${icon('shield',15)}<span>정밀 위치는 현재 검색에만 사용하며 백그라운드 추적은 하지 않습니다. 거부해도 수동 주소 입력이 가능합니다.</span></div>`;
    modal({
      title:consented?'출발지를 어떻게 정할까요?':'현재 위치를 사용할까요?',
      body,
      primary:consented?'현재 위치 사용':'동의하고 위치 확인',
      secondary:'주소 직접 입력',
      onPrimary:()=>{state.consents.location=true;store.set('er_consent_location','1');detectLocation(after);},
      onSecondary:()=>manualLocation(after)
    });
  }
  async function detectLocation(after) {
    toast('현재 위치를 확인하고 있어요.')
    try { const loc=await API.getCurrentLocation(); state.location={...state.location,...loc};state.locationConfirmed=true;rememberLocation(state.location);
      if(Number(loc.accuracyMeters)>500) toast(`위치 오차가 ${loc.accuracy}입니다. 정확한 출발지는 '다시 찾기 → 주소 직접 입력'을 쓰세요.`);
      else toast('현재 위치를 확인했습니다.');
      if(after)after(); else route(); }
    catch { modal({title:'자동 위치를 확인하지 못했어요',body:'<p>브라우저 위치 권한을 확인하거나 주소를 직접 입력해 주세요. 실제 좌표 없이 mock 위치로 검색하지 않습니다.</p>',primary:'주소 직접 입력',onPrimary:()=>manualLocation(after)}); }
  }
  async function applyManualLocation(keyword, after){
    const btn=modalRoot.querySelector('[data-manual-ok]');
    if(btn){ btn.disabled=true; btn.textContent='좌표를 찾는 중…'; }
    try{
      const place=await API.searchPlace(keyword);
      state.location={
        address:place.name||keyword, short:place.name||keyword,
        accuracy:'주소 입력', lat:place.latitude, lng:place.longitude
      };
      state.locationConfirmed=true;
      rememberLocation(state.location);
      closeModal();
      toast(`출발지를 '${place.name||keyword}'(으)로 설정했습니다.`);
      if(after)after(); else route();
    }catch(err){
      if(btn){ btn.disabled=false; btn.textContent='이 위치 사용'; }
      const message=err&&err.status===429?'Tmap 호출 한도를 초과해 주소를 좌표로 바꿀 수 없습니다.'
        :err&&err.status===404?'그 주소를 찾지 못했어요. 더 구체적으로 입력해 주세요.'
        :'주소 검색에 실패했습니다. 잠시 후 다시 시도해 주세요.';
      toast(message);
      console.warn('[app] 주소→좌표 변환 실패',err);
    }
  }
  function manualLocation(after) {
    modalRoot.innerHTML=`<div class="modal"><section class="sheet"><div class="handle"></div><h2>검색 위치 직접 입력</h2><p>주소, 지하철역 또는 건물명을 입력하세요.</p><input class="field" id="manual-address" placeholder="예: 강남역 1번 출구"><div class="sheet-actions" style="margin-top:12px"><button class="btn btn-primary btn-block" data-manual-ok>이 위치 사용</button><button class="btn btn-secondary btn-block" data-sheet-close>취소</button></div></section></div>`;
    modalRoot.querySelector('[data-manual-ok]').onclick=()=>{const v=document.getElementById('manual-address').value.trim();if(!v)return toast('검색 위치를 입력해 주세요.');      applyManualLocation(v,after);};
    modalRoot.querySelector('[data-sheet-close]').onclick=closeModal;
  }

  function renderSearch() {
    const profile=state.memberType==='member'?currentProfile():null;
    const availableProfiles=state.memberType==='member'?state.profiles:[];
    const profileButtons=[`<button class="profile-pick ${state.selectedProfileId==='manual'?'active':''}" data-profile-pick="manual"><span>${icon('edit',18)}</span><strong>직접 입력</strong><small>프로필 미사용</small></button>`,...availableProfiles.map(p=>`<button class="profile-pick ${state.selectedProfileId===p.id?'active':''}" data-profile-pick="${p.id}"><span>${icon(p.relation==='본인'?'user':'people',18)}</span><strong>${esc(p.name||p.relation)}</strong><small>${esc(p.relation)}${p.age?` · ${esc(p.age)}세`:''}</small></button>`)].join('');
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">응급실 찾기</p><h1 class="page-title">환자 상태를 알려주세요.</h1><p class="page-subtitle">증상과 상태를 알려주면 더 적합한 후보를 비교해드려요.</p></div><div class="risk-banner ${state.highRisk?'show':''}" data-risk-banner><span>${icon('alert',19)}</span><div><strong>긴급한 상태일 수 있어요.</strong><p>의식·호흡·대량출혈 등 위험 신호가 있으면 119 연락을 우선하세요. 병원 검색도 계속할 수 있습니다.</p><div class="risk-actions"><button class="btn btn-danger" style="min-height:36px;padding:0 12px" data-route="/emergency">119 연락</button></div></div></div><div class="card"><div class="field-label"><span>검색 위치</span><span style="color:var(--success);font-size:11px">필수</span></div><div class="search-location-weather"><button class="location-strip" style="width:100%;margin:0;text-align:left" data-location><span class="round-icon">${icon('pin',18)}</span><span class="location-text"><strong>${esc(state.locationConfirmed?state.location.short:'위치를 설정해 주세요')}</strong><span>${state.locationConfirmed?`정확도 ${esc(state.location.accuracy)} · 다시 찾기`:'GPS 또는 주소 직접 입력'}</span></span>${icon('location',19)}</button></div></div><div class="section-head"><div><h2 class="section-title">사고자</h2><p class="section-sub">본인 또는 가족 프로필을 선택하세요.</p></div><button class="text-btn" data-route="/profiles">${state.memberType==='member'?'프로필 관리':'회원 로그인'}</button></div><div class="profile-picker">${profileButtons}</div>${profile?`<div class="selected-profile-summary"><strong>${esc(profile.name||profile.relation)} 정보 적용</strong><span>${esc([profile.age&&`${profile.age}세`,profile.conditions&&`기저질환 ${profile.conditions}`,profile.allergies&&`알레르기 ${profile.allergies}`,profile.medications&&`복용약 ${profile.medications}`].filter(Boolean).join(' · ')||'등록된 의료 정보 없음')}</span></div>`:''}<div class="voice-card voice-card-with-memo"><button class="voice-button" data-quick-stt>${activeRecognition?icon('stop',22,'#fff'):icon('mic',22,'#fff')}</button><div class="voice-copy"><strong>음성으로 증상 말하기</strong><span>녹음 중 버튼을 한 번 더 누르면 멈춥니다. 원본 음성은 저장하지 않습니다.</span></div><div class="voice-live" data-voice-live><span></span><span></span><span></span><span></span><span></span></div><span class="voice-status" data-voice-status>${activeRecognition?'녹음 중':'선택'}</span><label class="voice-memo-field"><span class="field-label">상태 메모 <i class="optional">음성 전사·직접 입력</i></span><textarea class="field" id="memo" maxlength="180" placeholder="음성으로 말하거나 상태를 직접 입력하세요.">${esc(state.search.memo)}</textarea></label></div><div class="section-head"><div><h2 class="section-title">주요 증상</h2><p class="section-sub">복수 선택 가능</p></div><span class="optional">선택</span></div><div class="symptom-grid" id="symptom-chips">${DATA.symptoms.map(s=>`<button class="chip ${state.search.symptoms.includes(s)?'selected':''}" data-symptom="${s}">${s}</button>`).join('')}</div><div class="section-head"><div><h2 class="section-title">추가 정보</h2><p class="section-sub">모두 건너뛸 수 있습니다.</p></div><span class="optional">선택</span></div><div class="card"><div class="form-grid"><label><span class="field-label">환자 연령 <i class="optional">선택</i></span><input type="text" class="field" id="age" placeholder="예: 25, 50세" maxlength="10"></label><label><span class="field-label">의식 상태 <i style="color:var(--success);font-size:11px">필수</i></span><select class="field" id="conscious"><option value="">선택</option><option>또렷함</option><option>반응이 느림</option><option>잘 모르겠음</option></select></label><label class="full"><span class="field-label">호흡 상태 <i style="color:var(--success);font-size:11px">필수</i></span><select class="field" id="breathing"><option value="">선택</option><option>평소와 같음</option><option>숨쉬기 어려움</option><option>잘 모르겠음</option></select></label></div></div><div class="data-note">선택한 가족 프로필과 현재 입력 내용은 임상·자원 적합성, 가용성, ETA·교통, 최신성, 경로 안정성 비교에 활용됩니다.</div></div>`,{title:'응급실 찾기',subtitle:'',tab:'find',action:`<div class="fixed-action-bar with-tabs search-find-bar"><button class="btn btn-primary btn-block search-find-button" data-search>${icon('pin',18,'#fff')} 찾기</button></div>`});
    ['age','conscious','breathing'].forEach(id=>{document.getElementById(id).value=state.search[id]||'';document.getElementById(id).onchange=syncSearch;});
    document.getElementById('memo').oninput=syncSearch;
    app.querySelectorAll('[data-profile-pick]').forEach(b=>b.onclick=()=>{applyProfileToSearch(b.dataset.profilePick);renderSearch();bindCommon();});
    app.querySelectorAll('[data-symptom]').forEach(b=>b.onclick=()=>{b.classList.toggle('selected');const s=b.dataset.symptom;state.search.symptoms=b.classList.contains('selected')?[...new Set([...state.search.symptoms,s])]:state.search.symptoms.filter(x=>x!==s);detectHighRisk();});
    app.querySelector('[data-location]').onclick=()=>requestLocationConsent();
    app.querySelector('[data-quick-stt]').onclick=startQuickStt;
    app.querySelector('[data-search]').onclick=()=>{if(activeRecognition)stopActiveRecognition('녹음을 중단하고 검색합니다.');syncSearch();if(!state.search.conscious||!state.search.breathing)return toast('의식 상태와 호흡 상태는 필수입니다.');const next=async()=>{
      const btn=app.querySelector('[data-search]'); if(btn){btn.disabled=true;}
      toast('적합한 응급실을 찾고 있어요.');
      try{ await loadRecommendations(); go('/results'); }
      catch(err){ console.warn('[app] 추천 조회 실패',err); toast('추천을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.'); }
      finally{ if(btn) btn.disabled=false; }
    };if(!state.locationConfirmed)requestLocationConsent(next);else next();};
  }
  function syncSearch(){state.search.age=document.getElementById('age')?.value||'';state.search.conscious=document.getElementById('conscious')?.value||'';state.search.breathing=document.getElementById('breathing')?.value||'';state.search.memo=document.getElementById('memo')?.value.trim()||'';detectHighRisk();}
  function stopActiveRecognition(message='녹음을 멈췄습니다.'){
    if(!activeRecognition)return;
    const r=activeRecognition;activeRecognition=null;clearTimeout(activeRecognitionTimer);activeRecognitionTimer=null;try{r.stop();}catch{}
    const btn=app.querySelector('[data-quick-stt]'),status=app.querySelector('[data-voice-status]');btn?.classList.remove('recording');btn?.closest('.voice-card')?.classList.remove('recording');if(btn)btn.innerHTML=icon('mic',22,'#fff');if(status)status.textContent='전사 완료';if(message)toast(message);
  }
  function startQuickStt(){
    if(activeRecognition){stopActiveRecognition();return;}
    const btn=app.querySelector('[data-quick-stt]'),status=app.querySelector('[data-voice-status]'),memo=document.getElementById('memo');
    const R=window.SpeechRecognition||window.webkitSpeechRecognition;
    if(!R){return modal({title:'음성 입력을 사용할 수 없어요',body:'<p>이 브라우저에서는 음성 인식을 지원하지 않습니다. 상태 메모에 직접 입력해 주세요.</p>',primary:'상태 메모로 이동',onPrimary:()=>document.getElementById('memo')?.focus()});}
    const r=new R();activeRecognition=r;activeRecognitionBase=(memo?.value||'').trim();r.lang='ko-KR';r.interimResults=true;r.continuous=true;let finalParts=[];
    btn.classList.add('recording');btn.closest('.voice-card')?.classList.add('recording');btn.innerHTML=icon('stop',22,'#fff');status.textContent='녹음 중';toast('말씀해 주세요. 다시 누르면 녹음을 멈춥니다.');
    activeRecognitionTimer=setTimeout(()=>stopActiveRecognition('최대 녹음 시간이 지나 자동으로 종료했습니다.'),30000);
    r.onresult=e=>{let interim='';for(let i=e.resultIndex;i<e.results.length;i++){const t=e.results[i][0].transcript.trim();if(e.results[i].isFinal)finalParts.push(t);else interim+=t;}const combined=[activeRecognitionBase,...finalParts,interim].filter(Boolean).join(' ');if(memo)memo.value=combined;state.search.memo=combined;state.search.transcript=finalParts.join(' ');status.textContent=interim?'듣는 중':'녹음 중';detectHighRisk();};
    r.onerror=e=>{console.warn('[stt] SpeechRecognition error',e.error,location.origin);if(e.error!=='aborted')toast(`음성 전사 실패 (${e.error}). 상태 메모를 직접 입력해 주세요.`);};
    r.onend=()=>{if(activeRecognition===r){activeRecognition=null;clearTimeout(activeRecognitionTimer);activeRecognitionTimer=null;btn.classList.remove('recording');btn.closest('.voice-card')?.classList.remove('recording');btn.innerHTML=icon('mic',22,'#fff');status.textContent=finalParts.length?'전사 완료':'다시 시도';}};
    try{r.start();}catch{activeRecognition=null;toast('음성 녹음을 시작하지 못했습니다.');}
  }

  const CONGESTION={0:{c:'#9aa0b4',t:'정보없음'},1:{c:'#22a06b',t:'원활'},2:{c:'#e2a63b',t:'서행'},3:{c:'#e4762f',t:'지체'},4:{c:'#d63b3b',t:'정체'}};
  function congestionLegend(){
    return `<div class="map-legend">${[1,2,3,4].map(c=>`<span><i style="background:${CONGESTION[c].c}"></i>${CONGESTION[c].t}</span>`).join('')}</div>`;
  }

  function mapMarkup(activeId='', detailed=false) {
    const list=detailed&&activeId?[getResult(activeId)].filter(Boolean):(state.results.length?state.results:DATA.hospitals.slice(0,3));
    const hasRoute=list.some(h=>h&&h.raw&&h.raw.route);
    return `<div class="map-wrap" data-map><span class="map-label">현재 위치 기준 · ${formatTime(state.refreshedAt)} 갱신</span><div class="map-live" data-map-live></div><div class="map-fallback" data-map-fallback>${icon('map',22)}<span>지도를 불러오는 중…</span></div>${hasRoute?congestionLegend():''}</div>`;
  }


  function mountLiveMap(activeId='', detailed=false){
    const wrap=app.querySelector('[data-map]'), host=app.querySelector('[data-map-live]');
    if(!wrap||!host||!window.APP_MAP||!window.APP_API) return;
    const center=window.APP_API.currentSearchCenter&&window.APP_API.currentSearchCenter();
    const list=detailed&&activeId?[getResult(activeId)].filter(Boolean):state.results;
    const fallback=app.querySelector('[data-map-fallback]');
    const fail=msg=>{ if(fallback) fallback.innerHTML=`${icon('alert',22)}<span>${esc(msg)}</span>`; };
    if(!center||!list.length||!list.some(h=>h.raw)){ fail('위치 또는 병원 정보가 없어 지도를 표시할 수 없습니다.'); return; }
    window.APP_MAP.render(host,{
      origin:{lat:center.lat,lng:center.lng},
      hospitals:list.map(h=>({id:h.id,name:h.name,lat:h.lat,lng:h.lng,rank:h.rank,best:h.best,raw:h.raw})),
      activeId:activeId||list[0].id,
      onSelect:detailed?null:id=>selectResult(id,{scroll:true})
    }).then(res=>{
      wrap.classList.add('live');

      const label=wrap.querySelector('.map-label');
      if(label&&res&&!res.hasRoute){
        label.textContent=(window.APP_API.routeBlockedReason&&window.APP_API.routeBlockedReason())
          ||'실제 경로를 불러오지 못해 직선거리 기준으로 표시합니다.';
      }
    }).catch(err=>{ console.warn('[app] 실제 지도를 불러오지 못했습니다.',err); fail('지도를 불러오지 못했습니다. 네트워크와 백엔드 연결을 확인해 주세요.'); });
  }


  function selectResult(id,{scroll=false}={}){
    if(!id||state.activeResult===id) return;
    state.activeResult=id; state.selected=id;
    app.querySelectorAll('[data-hospital-card]').forEach(c=>c.classList.toggle('active',c.dataset.hospitalCard===id));
    app.querySelectorAll('[data-map-hospital]').forEach(m=>m.classList.toggle('active',m.dataset.mapHospital===id));
    app.querySelectorAll('[data-dot]').forEach(d=>d.classList.toggle('active',d.dataset.dot===id));
    if(scroll) app.querySelector(`[data-hospital-card="${id}"]`)?.scrollIntoView({behavior:'smooth',inline:'center',block:'nearest'});
    mountLiveMap(id);
  }

  function bedSignalChip(h){
    const s=h.bedSignal; if(!s||!s.label) return '';
    const cls={queued:'warn',backlog:'danger',zero:'warn',unknown:'muted'}[s.level]||'';
    const title={
      queued:'음수로 보고된 값입니다. 대기 인원 추정으로 읽고 후보에는 남겨 둡니다.',
      backlog:'대기 인원이 많아 순위를 뒤로 두었습니다. 제외한 것은 아닙니다.',
      zero:'가용 병상 0으로 보고됐습니다. 수용 불가 확정은 아닙니다.',
      unknown:'병원이 값을 보고하지 않아 확인되지 않았습니다.'
    }[s.level]||'';
    return `<span class="bed-chip ${cls}" title="${esc(title)}">${esc(s.label)}${s.deprioritized?' · 후순위':''}</span>`;
  }

  function hospitalCard(h){
    const statusClass = h.confirmRequired ? 'pending' : (h.best ? 'primary' : '');
    const statusLabel = h.best ? '최우선 후보' : (h.confirmRequired ? '확인 필요' : h.status);
    const scoreHint = h.grade==='A'
      ? '필수 자원 보고가 확인된 기관(A등급)'
      : '필수 자원을 보고하지 않아 전화 확인이 필요한 기관(B등급)';
    return `<article class="hospital-slide ${h.best?'best':''} ${h.grade==='B'?'grade-b':''} ${h.deprioritized?'deprioritized':''} ${state.activeResult===h.id?'active':''}" data-hospital-card="${h.id}"><div class="hospital-top"><span class="rank-badge">${h.rank}</span><div class="hospital-name">${h.best?`<span class="best-badge">${icon('crown',13,'#fff')} BEST 추천</span>`:''}<h3>${esc(h.name)}</h3><p>${esc(h.centerType)} · ${esc(h.short)}</p></div><span class="status-pill ${statusClass}">${esc(statusLabel)}</span></div><div class="metric-grid"><div class="mini-metric"><span>예상 시간</span><strong>${h.eta}분</strong></div><div class="mini-metric"><span>거리</span><strong>${h.distanceKm.toFixed(1)}km</strong></div><div class="mini-metric" title="${scoreHint}"><span>종합 점수</span><strong>${h.total}점</strong></div></div>${bedSignalChip(h)}<div class="reason-mini">${h.reasons.slice(0,3).map(r=>`<span>${esc(r)}</span>`).join('')}</div><div class="freshness">${icon('clock',12)} 현재 데이터 기준 · ${agoText(h.updatedMinutes)} · ${esc(h.status)}</div><div class="hospital-actions single"><button class="btn btn-secondary report-button" data-report="${h.id}">${icon('info',16)} 추천 리포트</button></div></article>`;
  }


  function modeBanner(){
    const mode=state.recommendationMeta&&state.recommendationMeta.mode;
    if(!mode) return '';
    const top=[...mode.weights].sort((a,b)=>b.weight-a.weight).slice(0,3)
      .map(w=>`<span>${esc(w.label)} ${Math.round(w.weight)}%</span>`).join('');
    return `<div class="mode-banner ${mode.severe?'severe':'normal'}">
      <div class="mode-head">${icon(mode.severe?'alert':'check',16)}<strong>${esc(mode.label)}</strong></div>
      <div class="mode-weights">${top}</div>
      ${mode.severe?`<p class="mode-note">${esc(mode.disclaimer)}</p>`:''}
    </div>`;
  }


  function pipelineMarkup(){
    const steps=(state.recommendationMeta&&state.recommendationMeta.pipeline)||[];
    if(!steps.length) return '';
    return `<details class="pipeline-box"><summary>${icon('info',15)} 어떻게 골랐나요? (${steps.length}단계)</summary>
      <ol class="pipeline-list">${steps.map(s=>`<li><strong>${esc(s.label)}</strong><span>${esc(s.detail)}</span></li>`).join('')}</ol>
    </details>`;
  }


  function bestNotice(){
    const meta=state.recommendationMeta;
    if(!meta||!state.results.length||meta.hasBest) return '';
    return `<div class="radius-banner holder-banner">${icon('alert',15)}<span>필수 자원이 확인된 A등급 후보가 없어 <strong>BEST를 표시하지 않았습니다</strong>. 아래는 우선 확인 후보이니 출발 전 전화로 확인해 주세요.</span></div>`;
  }
  function getResult(id){return state.results.find(h=>h.id===id)||state.results[0];}

  function renderResults(){
 
    if(!state.results.length){ loadRecommendations().then(()=>renderResults()).catch(err=>console.warn('[app] 추천 조회 실패',err)); }
    app.innerHTML=shell(`<div class="page compact"><div class="risk-banner ${state.highRisk?'show':''}" data-risk-banner><span>${icon('alert',19)}</span><div><strong>고위험 신호가 감지되었습니다.</strong><p>119 연락을 우선하고, 아래 병원 후보는 보조 정보로 확인하세요.</p><div class="risk-actions"><button class="btn btn-danger" style="min-height:36px;padding:0 12px" data-route="/emergency">119 연락</button></div></div></div>${(DATA.currentWeather&&DATA.currentWeather.rainRisk>=1)?`<div class="radius-banner rain-banner">${icon('rain',15)}<span>비 소식이 있어요. 외출 전 우산을 챙기세요.</span></div>`:''}<div class="results-head"><div><p class="eyebrow">추천 결과</p><h1 class="page-title" style="margin-bottom:4px">적합 후보 ${state.results.length}곳</h1><p class="page-subtitle">의료 자원과 실제 도착 가능성을 함께 비교했어요.</p></div><div class="results-tools"><button class="refresh-btn" data-refresh>${icon('refresh',16)} 갱신</button>${DATA.currentWeather?`<div class="results-weather-mini" aria-label="현재 날씨"><span>날씨 :</span>${icon(DATA.currentWeather.icon||'clear',20)}<strong>${esc(DATA.currentWeather.label||'')}</strong></div>`:''}</div></div>${modeBanner()}${state.radiusExpanded?`<div class="radius-banner">${icon('info',15)}<span>초기 5km 안에 적합 후보가 부족해 검색 범위를 ${state.radiusKm}km까지 확대했습니다.</span></div>`:''}${bestNotice()}${resourceHolderBanner()}${pipelineMarkup()}${mapMarkup(state.activeResult)}<div class="hospital-carousel" id="hospital-carousel">${state.results.map(hospitalCard).join('')}</div><div class="carousel-dots">${state.results.map(h=>`<span class="${state.activeResult===h.id?'active':''}" data-dot="${h.id}"></span>`).join('')}</div><div class="data-note">순위는 종합 점수 순입니다. 점수에는 자원 적합성·병상 여유·도착시간·응급센터 역량·데이터 최신성이 함께 반영됩니다. 병상이 0이거나 음수로 보고된 곳도 후보에는 남겨 두고 감점만 했습니다(음수는 대기 인원 표기로 봅니다). 다만 B등급은 필요한 병상을 병원이 보고하지 않아 확인되지 않은 곳이니, 점수가 높아도 출발 전 전화로 확인해 주세요.</div></div>`,{title:'추천 결과',subtitle:`${formatTime(state.refreshedAt)} 기준`,tab:'find',action:`<div class="fixed-action-bar with-tabs result-select-bar"><button class="btn btn-primary btn-block" data-confirm-hospital>선택한 병원으로 이동 ${icon('arrow',18,'#fff')}</button></div>`});
    bindResults();
    mountLiveMap(state.activeResult);
  }


  function cfgRadius(){ return (window.APP_CONFIG&&window.APP_CONFIG.radiusKm)||20; }
  function bindResults(){
    const carousel=document.getElementById('hospital-carousel');
    const setActive=id=>selectResult(id);
    app.querySelectorAll('[data-hospital-card]').forEach(c=>c.onclick=e=>{if(e.target.closest('button'))return;setActive(c.dataset.hospitalCard);});
    let pending=false;
    const syncActive=()=>{pending=false;const mid=carousel.scrollLeft+carousel.clientWidth/2;let best=null,dist=Infinity;
      carousel.querySelectorAll('[data-hospital-card]').forEach(c=>{const d=Math.abs(c.offsetLeft+c.offsetWidth/2-mid);if(d<dist){dist=d;best=c;}});
      if(best)setActive(best.dataset.hospitalCard);};
    carousel?.addEventListener('scroll',()=>{if(pending)return;pending=true;
      if(document.hidden)setTimeout(syncActive,60);else requestAnimationFrame(syncActive);},{passive:true});
    app.querySelectorAll('[data-map-hospital]').forEach(m=>m.onclick=()=>selectResult(m.dataset.mapHospital,{scroll:true}));
    app.querySelectorAll('[data-report]').forEach(b=>b.onclick=e=>{e.stopPropagation();go(`/report/${b.dataset.report}`);});
    app.querySelector('[data-confirm-hospital]').onclick=()=>{const id=state.activeResult||state.results[0]?.id;if(!id)return toast('병원을 먼저 선택해 주세요.');state.selected=id;saveRecentHospital(getResult(id));go(`/transport/${id}`);};
    const refresh=app.querySelector('[data-refresh]');refresh.onclick=async()=>{refresh.classList.add('loading');refresh.disabled=true;toast('병원·교통 정보를 다시 확인하고 있어요.');
      try{ await loadRecommendations(); renderResults(); toast('추천 순위와 갱신 시각을 업데이트했습니다.'); }
      catch(err){ console.warn('[app] 갱신 실패, 직전 데이터를 유지합니다.',err); toast('최신 정보를 받지 못해 직전 데이터를 유지합니다.'); refresh.classList.remove('loading'); refresh.disabled=false; }};
  }


  function resourceHolderBanner(){
    const list=(state.recommendationMeta&&state.recommendationMeta.resourceHolders)||[];
    if(!list.length) return '';
    return list.map(n=>`<div class="radius-banner holder-banner">${icon('alert',15)}<span><strong>${esc(n.hospital_name)}</strong>${n.distance_km!=null?` (${Number(n.distance_km).toFixed(1)}km)`:''}에 ${esc(n.resource)} ${esc(n.value)} 기록이 있으나, ${esc(n.reason)}</span></div>`).join('');
  }


  function bedSignalDetail(h){
    const s=h.bedSignal; if(!s||s.level==='ok') return '';
    const rows=[];
    if(s.queued.length) rows.push(...s.queued.map(q=>[`${q.label}`,`대기 ${q.queue}명 추정 (원본 -${q.queue})`]));
    if(s.zero.length) rows.push(...s.zero.map(l=>[l,'가용 0 보고']));
    if(s.unknown.length) rows.push(...s.unknown.map(l=>[l,'정보 없음 · 전화 확인 필요']));
    if(!rows.length) return '';
    const policy=s.level==='backlog'
      ? '대기가 많아 순위를 뒤로 두었습니다. 제외한 것은 아닙니다.'
      : s.level==='queued'
        ? '음수는 가용 없음이 아니라 대기 인원 표기로 읽고, 대기 인원만큼 감점했습니다.'
        : s.level==='zero'
          ? '가용 0은 수용 불가 확정이 아니므로 후보로 두고 경미하게 감점했습니다.'
          : '값이 없어 B등급(확인 필요)으로 두었습니다.';
    return `<div class="section-head"><div><h2 class="section-title">병상 상태 해석</h2></div></div>
      <div class="reading-table">${rows.map(([n,v])=>`<div class="reading-row"><span class="reading-name">${esc(n)}</span><span class="reading-value">${esc(v)}</span></div>`).join('')}</div>
      <p class="score-formula">${esc(policy)}</p>`;
  }


  function resourceReadingsMarkup(h){
    const rows=(h.evidence&&h.evidence.resource_readings)||[];
    if(!rows.length) return '';

    const valueText=v=>/^\d+개$/.test(v||'')?v.slice(0,-1):esc(v||'-');
    return `<div class="section-head"><div><h2 class="section-title">자원 판독</h2></div></div>
      <div class="reading-table">${rows.map(r=>`<div class="reading-row"><span class="reading-name">${esc(r.label||r.column)}</span><span class="reading-value">${valueText(r.value_text)}</span></div>`).join('')}</div>`;
  }


  function renderReport(id){
    const h=getResult(id); if(!h)return go('/results');state.selected=h.id;

    if(!h.evidence && !state.reportLoading){
      state.reportLoading=true;
      loadRecommendations({detail:true}).then(()=>{state.reportLoading=false;renderReport(id);})
        .catch(err=>{state.reportLoading=false;console.warn('[app] 리포트 상세 조회 실패',err);});
    }
    const metrics=[['clock','이동 시간',`${h.eta}분 · ${h.distanceKm.toFixed(1)}km`],['check','가용 정보',`${h.status} · ${agoText(h.updatedMinutes)}`],['heart','필요 자원',state.required.bedLabel],['medical','응급센터',h.centerType]];

    const labelMap={clinical_fit:'진료 적합도',availability:'응급실 이용 가능성',eta_traffic:'이동 편의성',center_capability:'응급센터 역량',freshness:'정보 신뢰도',route_weather_stability:'이동 환경'};
    const scores=(h.scoreBreakdown||[]).map(s=>[labelMap[s.key]||s.label,s.score,s.max_score,s.formula]);
    const filteredReasons=h.reasons||[];
    const mode=state.recommendationMeta&&state.recommendationMeta.mode;

    const modeLine=mode?`<p class="score-mode ${mode.severe?'severe':''}">${esc(mode.label)} 기준 · ${mode.weights.map(w=>`${esc(w.label)} ${Math.round(w.weight)}%`).join(' · ')}</p>`:'';
    app.innerHTML=shell(`<div class="page compact report-shell"><div class="report-summary" style="${h.best?'':'background:linear-gradient(145deg,#7c78a8,#9a96bb)'}"><span class="best-badge">${icon(h.best?'crown':'info',13,'#fff')} ${h.best?'BEST 추천':h.grade==='B'?'우선 확인 후보':'추천 병원'}</span><h2>${esc(h.name)}</h2><p>${esc(h.address)}</p></div><div class="report-metrics">${metrics.map(([ico,t,v])=>`<div class="report-metric"><span class="metric-icon">${icon(ico,17)}</span><strong>${t}</strong><span>${esc(v)}</span></div>`).join('')}</div><div class="score-breakdown"><h3>종합 점수 ${h.total}점</h3>${modeLine}${scores.length?scores.map(([n,v,m,f])=>`<div class="score-row"><span>${esc(n)}</span><div class="score-track"><div class="score-fill" style="width:${m?Math.round((v/m)*100):0}%"></div></div><strong>${Math.round(v)}/${Math.round(m)}</strong></div>`).join(''):'<p class="score-formula">상세 근거를 불러오는 중…</p>'}</div>${bedSignalDetail(h)}<div class="section-head"><div><h2 class="section-title">추천 이유</h2></div></div><div class="reason-mini" style="flex-wrap:wrap;overflow:visible">${filteredReasons.map(r=>`<span>${esc(r)}</span>`).join('')}</div>${resourceReadingsMarkup(h)}${(h.warnings||[]).length?`<div class="resource-row unknown"><strong>주의</strong><span>${h.warnings.map(w=>esc(typeof w==='string'?w:(w.text||w.message||''))).join(' · ')}</span></div>`:''}<div class="ai-summary"><span class="round-icon" style="width:34px;height:34px;border-radius:11px;flex:0 0 auto">${icon('chat',17)}</span><p><strong style="display:block;margin-bottom:3px;color:var(--ink)">종합 의견</strong>${esc(h.summary||'상세 근거를 불러오는 중입니다.')}</p></div><div class="compact-actions"><button class="btn btn-primary" data-select-report>병원 선택</button><button class="btn btn-outline" data-call="${h.phone}">${icon('phone',17)} 응급실 전화</button></div><div class="notice" style="margin-top:11px">${icon('info',15)}<span>추천은 탐색 보조 정보이며 의료진 진단과 병원 수용 확정을 의미하지 않습니다.</span></div></div>`,{title:'추천 리포트',back:true,close:true,noTabs:true});
    app.querySelector('[data-select-report]').onclick=()=>{saveRecentHospital(h);go(`/transport/${h.id}`);};
    app.querySelector('[data-call]').onclick=()=>location.href=`tel:${h.phone}`;
    bindCommon();
  }

  function renderMap(id){
    const h=getResult(id);if(!h)return go('/results');state.activeResult=h.id;
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">추천 위치 지도</p><h1 class="page-title">경로와 데이터를 확인하세요.</h1><p class="page-subtitle">BEST는 왕관 마커로, B등급은 전화 확인 후보로 표시됩니다.</p></div>${mapMarkup(h.id,true)}<div class="card address-card"><div class="hospital-top"><span class="rank-badge">${h.rank}</span><div class="hospital-name"><h3>${esc(h.name)}</h3><p>${h.eta}분 · ${h.distanceKm.toFixed(1)}km · ${h.status}</p></div>${h.best?`<span class="best-badge" style="background:var(--warning)">${icon('crown',12,'#fff')} BEST</span>`:`<span class="grade-pill ${h.grade.toLowerCase()}">${h.grade}등급</span>`}</div><div class="address-row"><div><strong>병원 주소</strong><span>${esc(h.address)}</span></div><div class="small-actions"><button class="mini-btn" data-view="${esc(h.address)}">보기</button><button class="mini-btn" data-copy="${esc(h.address)}">${icon('copy',13)} 복사</button></div></div><div class="address-row"><div><strong>현재 위치</strong><span>${esc(state.location.address)}</span></div><div class="small-actions"><button class="mini-btn" data-view="${esc(state.location.address)}">보기</button><button class="mini-btn" data-copy="${esc(state.location.address)}">${icon('copy',13)} 복사</button></div></div><div class="data-note">실제 경로거리와 직선거리는 다를 수 있으며 교통 ETA는 갱신 시각에 따라 변동됩니다.</div></div><div class="compact-actions"><button class="btn btn-outline" data-call="${h.phone}">${icon('phone',17)} 전화</button><button class="btn btn-primary" data-map-select>병원 선택</button></div></div>`,{title:'지도 상세',subtitle:`추천 ${h.rank}위`,tab:'find'});
    app.querySelectorAll('[data-map-hospital]').forEach(m=>m.onclick=()=>renderMap(m.dataset.mapHospital));
    app.querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>copyText(b.dataset.copy));
    app.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>modal({title:'주소 정보',body:`<p>${esc(b.dataset.view)}</p>`,primary:'주소 복사',onPrimary:()=>copyText(b.dataset.view)}));
    app.querySelector('[data-call]').onclick=()=>location.href=`tel:${h.phone}`;
    app.querySelector('[data-map-select]').onclick=()=>go(`/transport/${h.id}`);
    bindCommon();
  }
  async function copyText(text){try{await navigator.clipboard.writeText(text);toast('주소를 복사했습니다.');}catch{toast('주소를 길게 눌러 복사해 주세요.');}}

  function renderTransport(id){
    const h=getResult(id);if(!h)return go('/results');state.selected=h.id;
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">이동 수단</p><h1 class="page-title">어떻게 이동하시겠어요?</h1><p class="page-subtitle">선택한 병원까지의 이동 방법을 선택해 주세요.</p></div>${state.highRisk?`<div class="risk-banner show"><span>${icon('alert',19)}</span><div><strong>고위험 신호가 있습니다.</strong><p>이동 수단 선택보다 119 연락을 우선하는 것이 안전할 수 있습니다.</p><div class="risk-actions"><button class="btn btn-danger" data-route="/emergency">119 연락</button></div></div></div>`:''}<div class="card"><div class="hospital-top"><span class="rank-badge">${h.rank}</span><div class="hospital-name"><h3>${esc(h.name)}</h3><p>${esc(h.short)}</p></div><span class="grade-pill ${h.grade.toLowerCase()}">${h.grade}등급</span></div><div class="metric-grid"><div class="mini-metric"><span>경로거리</span><strong>${h.distanceKm.toFixed(1)}km</strong></div><div class="mini-metric"><span>예상 시간</span><strong>${h.eta}분</strong></div><div class="mini-metric"><span>정보 상태</span><strong>${h.grade==='A'?'확인':'전화확인'}</strong></div></div></div><div class="section-head"><div><h2 class="section-title">이동 방법</h2><p class="section-sub">호출 직전에 정보 제공 동의를 확인합니다.</p></div></div><div class="transport-list"><button class="transport-card" data-transport="사설 응급차"><span class="round-icon">${icon('ambulance',22)}</span><span><h3>사설 응급차 연결</h3><p>현재 위치와 목적지를 호출업체에 전달</p></span>${icon('chevron',19)}</button><button class="transport-card" data-transport="택시"><span class="round-icon success">${icon('taxi',22)}</span><span><h3>택시 연결</h3><p>현재 위치와 목적지를 호출업체에 전달</p></span>${icon('chevron',19)}</button><button class="btn btn-secondary btn-block" data-transport="직접 이동">직접 이동하기</button></div></div>`,{title:'이동 수단',subtitle:'',tab:'find'});
    app.querySelectorAll('[data-transport]').forEach(b=>b.onclick=()=>{const type=b.dataset.transport;if(type==='사설 응급차')return go(`/ambulance/${h.id}`);if(type==='직접 이동')return go(`/complete/${encodeURIComponent(type)}`);modal({title:`${type} 정보 제공 동의`,body:`<p><strong>수신자:</strong> ${type} 호출 제휴사<br><strong>목적:</strong> ${type} 배차 및 경로 안내<br><strong>제공 항목:</strong> 현재 위치, 목적지 병원<br><strong>보유:</strong> 호출 완료 후 제휴사 정책에 따라 파기</p><div class="notice">${icon('info',15)}<span>동의하지 않아도 직접 이동과 응급실 전화 기능을 사용할 수 있습니다.</span></div>`,primary:'동의하고 연결',onPrimary:async()=>{await API.createTransportRequest({type,hospitalId:h.id,location:state.location});go(`/complete/${encodeURIComponent(type)}`);}});});
    bindCommon();
  }

  function renderAmbulance(id){
    const h=getResult(id);if(!h)return go('/results');state.selected=h.id;
    const list=DATA.privateAmbulances||[];
    if(!state.selectedAmbulance&&list.length)state.selectedAmbulance=list[0].id;
    const cards=list.map(a=>`<button class="ambulance-option ${state.selectedAmbulance===a.id?'active':''}" data-ambulance="${a.id}"><span class="ambulance-radio"></span><span class="ambulance-company"><strong>${esc(a.name)}</strong><small>${esc(a.vehicle)} · ${esc(a.crew)}</small><span class="ambulance-tags">${a.tags.map(t=>`<i>${esc(t)}</i>`).join('')}</span></span><span class="ambulance-meta"><strong>${a.arrivalMin}분</strong><small>예상 도착</small><b>${a.fee.toLocaleString('ko-KR')}원~</b></span></button>`).join('');
    app.innerHTML=shell(`<div class="page compact ambulance-page"><div class="route-summary"><div class="route-points"><span class="route-point start"></span><div><small>출발</small><strong>${esc(state.location.short)}</strong></div><span class="route-line"></span><span class="route-point end"></span><div><small>목적지</small><strong>${esc(h.name)}</strong></div></div><div class="route-stats"><span>${icon('clock',15)} 약 ${h.eta}분</span><span>${icon('map',15)} ${h.distanceKm.toFixed(1)}km</span></div></div><div class="ambulance-map">${mapMarkup(h.id,true)}<div class="ambulance-route-badge">${icon('ambulance',16)} 목적지까지 이송 경로</div></div><div class="section-head ambulance-section-head"><div><h2 class="section-title">등록된 사설 응급차</h2><p class="section-sub">도착 예정 시간과 기본 장비를 비교하세요.</p></div><span class="optional">${list.length}대</span></div><div class="ambulance-list">${cards}</div><div class="notice ambulance-notice">${icon('info',15)}<span>표시 금액은 시연용 예상 범위입니다. 실제 요금과 배차 가능 여부는 업체 확인 후 확정됩니다.</span></div></div>`,{title:'사설 응급차 연결',subtitle:'',noTabs:true,action:`<div class="fixed-action-bar no-tabs-action ambulance-call-bar"><button class="btn btn-primary btn-block" data-call-ambulance>${icon('ambulance',19,'#fff')} 선택한 응급차 호출</button></div>`});
    const choose=id=>{state.selectedAmbulance=id;app.querySelectorAll('[data-ambulance]').forEach(c=>c.classList.toggle('active',c.dataset.ambulance===id));};
    app.querySelectorAll('[data-ambulance]').forEach(c=>c.onclick=()=>choose(c.dataset.ambulance));
    app.querySelectorAll('[data-map-hospital]').forEach(m=>m.onclick=()=>toast(`${h.name} 목적지로 설정되어 있습니다.`));
    app.querySelector('[data-call-ambulance]').onclick=()=>{
      const a=list.find(x=>x.id===state.selectedAmbulance);if(!a)return toast('호출할 사설 응급차를 선택해 주세요.');
      modal({title:'사설 응급차 정보 제공 동의',body:`<p><strong>호출 업체:</strong> ${esc(a.name)}<br><strong>목적:</strong> 환자 이송 배차 및 경로 안내<br><strong>제공 항목:</strong> 현재 위치, 목적지 병원, 연락용 임시 식별자<br><strong>예상 도착:</strong> 약 ${a.arrivalMin}분</p><div class="notice">${icon('info',15)}<span>동의하지 않아도 병원 비교와 직접 이동 기능을 계속 사용할 수 있습니다.</span></div>`,primary:'동의하고 호출',secondary:'취소',onPrimary:async()=>{await API.createTransportRequest({type:'사설 응급차',providerId:a.id,hospitalId:h.id,location:state.location});go(`/complete/${encodeURIComponent(`${a.name} 사설 응급차`)}`);}});
    };
    bindCommon();
  }

  function renderComplete(type='이동'){
    const h=getResult(state.selected)||state.results[0];
    app.innerHTML=shell(`<div class="page" style="text-align:center;padding-top:68px"><span class="round-icon success" style="width:72px;height:72px;border-radius:24px;margin:0 auto 18px">${icon('check',34)}</span><p class="eyebrow">요청 준비 완료</p><h1 class="page-title">${esc(type)} 요청을 준비했어요.</h1><p class="page-subtitle">목적지: ${esc(h?.name||'선택 병원')}<br>실제 백엔드 연결 시 요청 상태와 취소 기능을 제공합니다.</p><div style="display:grid;gap:9px;margin-top:24px"><button class="btn btn-primary btn-block" data-route="/home">홈으로</button><button class="btn btn-secondary btn-block" data-route="/results">추천 결과 다시 보기</button></div></div>`,{title:'이동 준비 완료',noTabs:true});
    bindCommon();
  }

  function renderAIAccess(){
    if(state.memberType!=='member'){
      app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">AI진단</p><h1 class="page-title">회원 로그인이 필요해요.</h1><p class="page-subtitle">응급실 찾기와 고위험 신호 확인은 로그인 없이 계속 이용할 수 있습니다.</p></div><div class="subscription-card"><h2>AI진단는 회원 기능입니다.</h2><p>구조화 질문과 상담 기록은 건강정보를 처리하므로 회원 확인 후 제공합니다.</p><div class="subscription-list"><span>${icon('check',15,'#fff')} 응급실 찾기는 계속 무료</span><span>${icon('check',15,'#fff')} 고위험 신호 119 전환은 무료</span></div><button class="btn btn-block" data-route="/login">로그인하기</button></div><button class="btn btn-secondary btn-block" style="margin-top:12px" data-route="/search">응급실 찾기로 이동</button></div>`,{title:'AI진단',subtitle:'회원 전용',tab:'chat'});return;
    }
    if(false){
      app.innerHTML=shell(`<div class="page compact"><div class="subscription-card"><h2>구독 AI진단</h2><p>진단 대신 상황을 정리하고 최대 4개의 질문으로 필요한 응급 자원과 검색 조건을 생성합니다.</p><div class="subscription-list"><span>${icon('check',15,'#fff')} 가족 응급 프로필·체크리스트</span><span>${icon('check',15,'#fff')} 상담 → 응급실 검색 자동 인계</span><span>${icon('check',15,'#fff')} 다국어·음성 상황 요약</span></div><button class="btn btn-block" data-subscribe-demo>구독 데모 활성화</button></div><div class="notice" style="margin-top:12px">${icon('info',16)}<span>구독하지 않아도 응급실 찾기, Push-to-Talk, 119 연락은 제한되지 않습니다.</span></div><button class="btn btn-secondary btn-block" style="margin-top:12px" data-route="/search">응급실 찾기</button></div>`,{title:'AI진단',subtitle:'구독 회원 전용',tab:'chat'});
      app.querySelector('[data-subscribe-demo]').onclick=()=>{state.memberType='subscriber';store.set('er_member_type','subscriber');toast('구독 데모를 활성화했습니다.');route();};return;
    }
    if(!state.consents.health)return renderHealthConsent();
    renderChatbot();
  }

  function renderHealthConsent(){
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">AI진단 첫 이용</p><h1 class="page-title">건강정보 처리를 확인해 주세요.</h1><p class="page-subtitle">필수 건강정보 동의와 선택 저장·품질개선 동의를 분리했습니다.</p></div><div class="consent-card"><label class="consent-row"><input type="checkbox" id="health-required"><span><strong>[필수] 건강·증상정보 수집·이용</strong><span>증상 분석, 응급자원 매핑, 상담 답변 생성에 사용합니다. 거부 시 AI진단만 제한됩니다.</span></span></label><label class="consent-row"><input type="checkbox" id="record-opt"><span><strong>[선택] 상담 기록 저장·개인화</strong><span>선택하지 않으면 세션 종료 후 상담 내용을 계정 이력에 저장하지 않습니다.</span></span></label><label class="consent-row"><input type="checkbox" id="quality-opt"><span><strong>[선택] 비식별 AI 품질 개선</strong><span>기본 OFF이며 거부해도 AI진단를 사용할 수 있습니다. 원본 음성은 학습에 사용하지 않습니다.</span></span></label></div><div class="notice" style="margin-top:12px">${icon('info',16)}<span>본 기능은 진단·처방을 제공하지 않으며 의료진을 대체하지 않습니다.</span></div><button class="btn btn-primary btn-block" style="margin-top:14px" data-health-start>동의하고 시작</button></div>`,{title:'건강정보 동의',subtitle:'AI진단 사용 직전',noTabs:true});
    app.querySelector('[data-health-start]').onclick=()=>{if(!document.getElementById('health-required').checked)return toast('필수 건강정보 동의를 확인해 주세요.');state.consents.health=true;state.consents.record=document.getElementById('record-opt').checked;state.consents.aiQuality=document.getElementById('quality-opt').checked;store.set('er_consent_health','1');store.set('er_consent_record',state.consents.record?'1':'0');store.set('er_consent_ai_quality',state.consents.aiQuality?'1':'0');go('/chatbot');};
  }

  function evaluateAIPolicy(text=''){
    const t=String(text).trim(); const lower=t.toLowerCase();
    const has=(arr=[])=>(arr||[]).some(k=>t.includes(k));
    if(has(PROMPT.promptAttackTerms))return {intent:'PROMPT_ATTACK',confidence:.99,level:null,redFlags:[],override:false,kind:'prompt_attack'};
    if(has(PROMPT.nonMedicalTerms))return {intent:'NON_MEDICAL',confidence:.9,level:null,redFlags:[],override:false,kind:'non_medical'};
    if(has(PROMPT.medicationDoseTerms))return {intent:'MED_MEDICATION',confidence:.9,level:null,redFlags:[],override:false,kind:'medication_boundary'};
    if(has(PROMPT.diagnosisTerms))return {intent:'MED_DISEASE_INFO',confidence:.88,level:null,redFlags:[],override:false,kind:'diagnosis_boundary'};
    const flags=[];let level=null;
    for(const r of (PROMPT.redFlagRules||[]))if((r.terms||[]).some(k=>t.includes(k))){flags.push(r.code);if(r.level==='AI-T1')level='AI-T1';else if(!level)level=r.level;}
    if(/가슴|흉통/.test(t)&&/숨|호흡/.test(t)){if(!flags.includes('CARDIAC'))flags.push('CARDIAC');if(level!=='AI-T1')level='AI-T2';}
    return {intent:flags.length?'EMERGENCY_DIRECT':'MED_SYMPTOM',confidence:flags.length?.95:.84,level,redFlags:flags,override:!!flags.length,kind:flags.length?'emergency':'medical'};
  }
  function appendBotMessage(stream,html,cls='') { const n=document.createElement('div');n.innerHTML=`<div class="chat-message ${cls}"><span class="avatar">${icon('chat',17,'#fff')}</span><div class="bubble ${cls}">${html}</div></div>`;stream.appendChild(n);stream.scrollTo({top:stream.scrollHeight,behavior:'smooth'});return n; }
  function showSttVerify(phrase,input,onRetry){modalRoot.innerHTML=`<div class="modal"><section class="sheet" role="dialog" aria-modal="true"><div class="handle"></div><h2>음성 내용을 확인해 주세요</h2><p>핵심 의료 표현의 음성 인식 신뢰도가 낮아 재확인이 필요합니다.</p><div class="stt-verify-phrase">“${esc(phrase)}”</div><div class="stt-verify-actions"><button class="btn btn-primary" data-stt-yes>맞아요</button><button class="btn btn-secondary" data-stt-retry>다시 말하기</button><button class="btn btn-outline" data-stt-edit>직접 수정</button></div></section></div>`;modalRoot.querySelector('[data-stt-yes]').onclick=()=>closeModal();modalRoot.querySelector('[data-stt-retry]').onclick=()=>{input.value='';closeModal();onRetry?.();};modalRoot.querySelector('[data-stt-edit]').onclick=()=>{closeModal();input.focus();input.setSelectionRange(input.value.length,input.value.length);};}

  function renderChatbot(){
    state.chatAnswers={};
    app.innerHTML=`<section class="app-screen">${header('AI진단','',{emergency:true})}<div class="chat-wrap"><div class="chat-scroll" id="chat-scroll"><div class="chat-message"><span class="avatar">${icon('chat',17,'#fff')}</span><div class="bubble">${esc(PROMPT.intro)}</div></div></div><div class="chat-voice-status" data-chat-voice-status>녹음 중 · 다시 누르면 정지</div><div class="chat-compose"><input id="chat-input" placeholder="증상을 입력하세요"><button class="chat-voice" data-voice>${icon('mic',18)}</button><button class="send" data-send>${icon('send',18,'#fff')}</button></div></div>${tabs('chat')}</section>`;
    const stream=document.getElementById('chat-scroll');let idx=0,stopped=false;
    const emergencyTransition=(ev)=>{stopped=true;state.highRisk=true;stopChatRecognition(false);appendBotMessage(stream,`<strong>긴급 안내</strong><br>현재 말씀하신 내용은 즉시 응급 평가가 필요한 상황일 수 있습니다.<div class="ai-emergency-actions"><button class="btn btn-danger" data-route="/emergency">119 연락</button><button class="btn btn-secondary" data-route="/search">응급실 찾기</button></div><div class="ai-policy-note">AI Triage ${ev.level||'AI-T2'} · ${ev.redFlags.join(', ')||'Red Flag'} · 확정 진단이 아닙니다.</div>`,'ai-emergency-response');bindCommon();};
    const ask=()=>{if(stopped)return;const q=DATA.chat[idx];if(!q||idx>=(PROMPT.maxQuestions||4))return finish();const wrap=document.createElement('div');wrap.innerHTML=`<div class="chat-message"><span class="avatar">${icon('chat',17,'#fff')}</span><div class="bubble">${esc(q.q)}</div></div><div class="chat-options">${q.options.map(o=>`<button class="chip" data-answer="${esc(o)}">${esc(o)}</button>`).join('')}</div>`;stream.appendChild(wrap);wrap.querySelectorAll('[data-answer]').forEach(b=>b.onclick=()=>{state.chatAnswers[q.key]=b.dataset.answer;wrap.querySelectorAll('.chip').forEach(x=>{x.disabled=true;x.classList.toggle('selected',x===b);});const answer=b.dataset.answer;const direct=(q.key==='conscious'&&answer==='아니요')||(q.key==='breathing'&&answer==='아니요');const ev=evaluateAIPolicy(answer);if(direct||ev.override){emergencyTransition(direct?{level:'AI-T1',redFlags:[q.key==='conscious'?'CONSCIOUSNESS':'AIRWAY_BREATHING']}:ev);return;}idx++;setTimeout(ask,180);});stream.scrollTo({top:stream.scrollHeight,behavior:'smooth'});};
    const finish=()=>{const symptom=state.chatAnswers.symptom;if(symptom&&DATA.symptoms.includes(symptom))state.search.symptoms=[...new Set([...state.search.symptoms,symptom])];appendBotMessage(stream,`<strong>상황 정리가 완료되었습니다.</strong><br>확정 진단이 아니라 응급실 탐색을 위한 상황 정리 결과입니다.<div style="margin-top:10px"><button class="btn btn-primary" data-route="/search">검색 조건 확인</button></div>`);bindCommon();};
    const sendMessage=async()=>{const input=document.getElementById('chat-input'),v=input.value.trim();if(!v)return toast('메시지를 입력해 주세요.');stopChatRecognition(false);const u=document.createElement('div');u.innerHTML=`<div class="chat-message user"><div class="bubble">${esc(v)}</div></div>`;stream.appendChild(u);input.value='';const ev=evaluateAIPolicy(v);if(ev.kind==='prompt_attack'){appendBotMessage(stream,'내부 시스템 프롬프트나 정책 우회 정보는 제공할 수 없습니다. 응급·의료 증상이 있다면 말씀해 주세요.');return;}if(ev.kind==='non_medical'){appendBotMessage(stream,'저는 응급·의료 증상 분석과 의료기관 안내를 돕는 AI입니다. 몸이 불편하거나 응급 상황이 있다면 증상을 말씀해 주세요.');return;}if(ev.kind==='medication_boundary'){appendBotMessage(stream,'개별 약물 처방이나 복용량 변경은 안내하지 않습니다. 처방 의료진·약사 또는 제품 라벨을 확인해 주세요. 과다복용이 의심되면 즉시 응급 평가가 필요할 수 있습니다.');return;}if(ev.kind==='diagnosis_boundary'){appendBotMessage(stream,'현재 입력만으로 질환을 확정할 수 없습니다. 가능한 위험 범주를 확인하고 필요하면 의료진 평가와 응급실 탐색을 안내하겠습니다.');return;}if(ev.override){emergencyTransition(ev);return;}const thinking=appendBotMessage(stream,'분석 중...','thinking');const res=await API.sendAIGuidance(v);thinking.remove();if(!res.ok||!res.triage){appendBotMessage(stream,'AI 응답을 받지 못했습니다. 잠시 후 다시 시도하거나 응급실 찾기를 이용해 주세요.');return;}const msg=res.triage.user_message||JSON.stringify(res.triage);const audio=res.audioUrl?`<audio controls autoplay src="${esc(res.audioUrl)}" style="margin-top:8px;width:100%"></audio>`:'';appendBotMessage(stream,`${esc(msg)}${audio}`);modal({title:'증상 저장',body:'<p>말씀하신 증상을 응급실 찾기 창의 상태 메모에 저장하시겠습니까?</p>',primary:'예, 저장',secondary:'아니오',onPrimary:()=>{state.search.memo=[state.search.memo,v].filter(Boolean).join(' ');toast('상태 메모에 저장되었습니다.');}});};
    const stopChatRecognition=(notify=true)=>{if(!chatRecognition)return;const r=chatRecognition;chatRecognition=null;clearTimeout(chatRecognitionTimer);chatRecognitionTimer=null;try{r.stop();}catch{}const b=app.querySelector('[data-voice]');b?.classList.remove('recording');if(b)b.innerHTML=icon('mic',18);app.querySelector('[data-chat-voice-status]')?.classList.remove('show');if(notify)toast('음성 녹음을 멈췄습니다.');};
    const toggleChatVoice=()=>{if(chatRecognition){stopChatRecognition(true);return;}const R=window.SpeechRecognition||window.webkitSpeechRecognition;if(!R)return toast('이 브라우저에서는 음성 입력을 지원하지 않습니다.');const input=document.getElementById('chat-input'),b=app.querySelector('[data-voice]'),status=app.querySelector('[data-chat-voice-status]');const r=new R();chatRecognition=r;r.lang='ko-KR';r.interimResults=true;r.continuous=true;let finals=[],minConfidence=1;const base=input.value.trim();b.classList.add('recording');b.innerHTML=icon('stop',18,'#fff');status.classList.add('show');chatRecognitionTimer=setTimeout(()=>stopChatRecognition(true),30000);r.onresult=e=>{let interim='';for(let i=e.resultIndex;i<e.results.length;i++){const res=e.results[i],t=res[0].transcript.trim();if(res.isFinal){finals.push(t);minConfidence=Math.min(minConfidence,Number(res[0].confidence||1));}else interim+=t;}input.value=[base,...finals,interim].filter(Boolean).join(' ');};r.onerror=e=>{console.warn('[stt] SpeechRecognition error',e.error,location.origin);if(e.error!=='aborted')toast(`음성 전사 실패 (${e.error}). 텍스트로 입력해 주세요.`);};r.onend=()=>{if(chatRecognition===r){chatRecognition=null;clearTimeout(chatRecognitionTimer);chatRecognitionTimer=null;b.classList.remove('recording');b.innerHTML=icon('mic',18);status.classList.remove('show');const phrase=input.value.trim();const ev=evaluateAIPolicy(phrase);if(phrase&&minConfidence<(PROMPT.sttThreshold||.75)&&ev.redFlags.length)showSttVerify(phrase,input,toggleChatVoice);}};try{r.start();toast('말씀해 주세요. 다시 누르면 녹음을 멈춥니다.');}catch{chatRecognition=null;toast('음성 녹음을 시작하지 못했습니다.');}};
    app.querySelector('[data-voice]').onclick=toggleChatVoice;
    app.querySelector('[data-send]').onclick=sendMessage;
  }

  function renderEmergency(){
    app.innerHTML=shell(`<div class="page emergency-page"><div class="emergency-icon">${icon('phone',42)}</div><p class="eyebrow">119 긴급 연락</p><h1 class="page-title">즉시 119에 연락하세요.</h1><p class="page-subtitle">의식 저하, 정상 호흡 아님, 대량출혈, 전신 경련 등 생명 위협 가능성이 있으면 앱 탐색보다 긴급 신고를 우선하세요.</p><div class="card" style="text-align:left;margin-top:20px"><div class="field-label">신고 위치 참고</div><div class="location-strip" style="margin:0"><span class="round-icon">${icon('pin',18)}</span><div class="location-text"><strong>${esc(state.location.short)}</strong><span>${esc(state.location.address)} · ${esc(state.location.accuracy)}</span></div></div></div><div class="center-actions"><a class="btn btn-danger btn-block" href="tel:119">${icon('phone',20,'#fff')} 119 전화 연결</a><button class="btn btn-outline btn-block" data-copy-location>${icon('copy',17)} 현재 위치 복사</button><button class="btn btn-secondary btn-block" data-route="/search">응급실 검색도 계속하기</button></div><div class="notice" style="margin-top:14px">${icon('info',15)}<span>통화 기능이 제한된 기기에서는 직접 119를 입력해 발신하세요.</span></div></div>`,{title:'119 긴급 연락',subtitle:'로그인·구독 확인 없음',noTabs:true});
    app.querySelector('[data-copy-location]').onclick=()=>copyText(state.location.address);
    bindCommon();
  }

  function renderAccount(){
    app.innerHTML=shell(`<div class="page compact"><div class="page-head"><p class="eyebrow">계정·동의 설정</p><h1 class="page-title">${memberLabel()}</h1><p class="page-subtitle">핵심 응급 기능은 계정 상태와 무관하게 유지됩니다.</p></div><div class="policy-card"><h3>위치 자동 조회</h3><p>${state.consents.location?'동의 완료':'미동의 · 주소 직접 입력 가능'}</p></div><div class="policy-card"><h3>AI 건강정보</h3><p>${state.consents.health?'동의 완료':'미동의 · AI진단 이용 시 별도 확인'}</p></div><div class="policy-card"><h3>상담 기록 저장</h3><p>${state.consents.record?'선택 동의':'저장하지 않음'}</p></div><div class="policy-card"><h3>AI 품질 개선</h3><p>${state.consents.aiQuality?'선택 동의':'기본 OFF'}</p></div><button class="btn btn-outline btn-block" style="margin-top:14px" data-reset-consents>선택 동의 초기화</button><button class="btn btn-secondary btn-block" style="margin-top:9px" data-logout>로그아웃</button></div>`,{title:'계정·동의',subtitle:'설정',noTabs:true});
    app.querySelector('[data-reset-consents]').onclick=()=>{['er_consent_location','er_consent_health','er_consent_record','er_consent_ai_quality'].forEach(k=>store.remove(k));state.consents={location:false,health:false,record:false,aiQuality:false};toast('동의 상태를 초기화했습니다.');route();};
    app.querySelector('[data-logout]').onclick=()=>{['er_member_type','er_user','er_auto_login'].forEach(k=>store.remove(k));state.memberType='guest';state.user='';go('/login');};
    bindCommon();
  }

  function bindCommon(){
    app.querySelectorAll('[data-route]').forEach(el=>el.onclick=()=>go(el.dataset.route));
    app.querySelectorAll('[data-back]').forEach(el=>el.onclick=()=>history.length>1?history.back():go('/home'));
    app.querySelectorAll('[data-close-report]').forEach(el=>el.onclick=()=>go('/results'));
    app.querySelectorAll('[data-account]').forEach(el=>el.onclick=()=>go('/account'));

    app.querySelectorAll('[data-go-119]').forEach(el=>el.onclick=()=>go('/emergency'));
  }

  function route(){
    const raw=location.hash.replace(/^#/,'')||'/login';const parts=raw.split('/').filter(Boolean),page=parts[0]||'login';
    if(page==='login'&&store.get('er_auto_login')==='1'&&state.memberType==='member'){go('/home');return;}const routes={login:renderLogin,signup:renderSignup,home:renderHome,profiles:renderProfiles,policy:renderPolicy,search:renderSearch,results:renderResults,report:()=>renderReport(parts[1]),map:()=>renderMap(parts[1]),transport:()=>renderTransport(parts[1]),ambulance:()=>renderAmbulance(parts[1]),complete:()=>renderComplete(decodeURIComponent(parts[1]||'이동')), 'ai-access':renderAIAccess,chatbot:renderChatbot,emergency:renderEmergency,account:renderAccount};
    (routes[page]||renderHome)();bindCommon();
  }

  if(state.locationConfirmed) rememberLocation(state.location);
  window.addEventListener('hashchange',route);route();
  window.APP_LIVE_REFRESH=()=>{const page=(location.hash.replace(/^#/,'')||'').split('/').filter(Boolean)[0];if(page==='results'||page==='report'){loadRecommendations().then(route).catch(err=>console.warn('[app] 실시간 갱신 재조회 실패',err));}};
  if('serviceWorker'in navigator&&location.protocol.startsWith('http'))navigator.serviceWorker.register('./sw.js').catch(()=>{});
})();
