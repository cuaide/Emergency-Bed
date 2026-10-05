(() => {
  const SOURCE_ROWS = [{"hospital_id": "snuh", "hospital_name": "서울대학교병원", "address": "서울특별시 종로구 대학로 101", "district": "종로구", "representative_phone": "02-2072-2114", "emergency_room_phone": "02-2072-2114", "emergency_center_type_name": "권역응급의료센터", "hospital_latitude": 37.5796, "hospital_longitude": 126.999, "available_er_beds": 8, "available_operating_rooms": 2, "available_general_icu_beds": 4, "available_internal_medicine_icu_beds": 3, "available_surgical_icu_beds": 2, "available_thoracic_icu_beds": 2, "available_neurology_inpatient_beds": 2, "available_neurosurgery_icu_beds": 2, "available_burn_icu_beds": 0, "available_trauma_icu_beds": 1, "available_drug_poisoning_icu_beds": 1, "updated_minutes_demo": 4, "route_distance_km": 4.8, "estimated_travel_time_sec": 900, "traffic_status": "보통", "current_rain_type": "없음", "rain_amount_mm": 0, "hospital_straight_km": 4.2, "map_x": 72, "map_y": 27}, {"hospital_id": "samsung", "hospital_name": "삼성서울병원", "address": "서울특별시 강남구 일원로 81", "district": "강남구", "representative_phone": "02-3410-2114", "emergency_room_phone": "02-3410-2114", "emergency_center_type_name": "지역응급의료센터", "hospital_latitude": 37.4883, "hospital_longitude": 127.0856, "available_er_beds": 5, "available_operating_rooms": 1, "available_general_icu_beds": 3, "available_internal_medicine_icu_beds": 2, "available_surgical_icu_beds": 2, "available_thoracic_icu_beds": 1, "available_neurology_inpatient_beds": 1, "available_neurosurgery_icu_beds": 1, "available_burn_icu_beds": 0, "available_trauma_icu_beds": 1, "available_drug_poisoning_icu_beds": null, "updated_minutes_demo": 9, "route_distance_km": 4.1, "estimated_travel_time_sec": 720, "traffic_status": "원활", "current_rain_type": "없음", "rain_amount_mm": 0, "hospital_straight_km": 3.6, "map_x": 84, "map_y": 57}, {"hospital_id": "severance", "hospital_name": "세브란스병원", "address": "서울특별시 서대문구 연세로 50", "district": "서대문구", "representative_phone": "02-2228-5800", "emergency_room_phone": "02-2228-5800", "emergency_center_type_name": "권역응급의료센터", "hospital_latitude": 37.5623, "hospital_longitude": 126.9408, "available_er_beds": 3, "available_operating_rooms": 2, "available_general_icu_beds": 2, "available_internal_medicine_icu_beds": 2, "available_surgical_icu_beds": 3, "available_thoracic_icu_beds": 1, "available_neurology_inpatient_beds": 2, "available_neurosurgery_icu_beds": 3, "available_burn_icu_beds": 1, "available_trauma_icu_beds": 2, "available_drug_poisoning_icu_beds": 1, "updated_minutes_demo": 18, "route_distance_km": 7.5, "estimated_travel_time_sec": 1380, "traffic_status": "혼잡", "current_rain_type": "비", "rain_amount_mm": 2.1, "hospital_straight_km": 6.3, "map_x": 54, "map_y": 79}, {"hospital_id": "asan", "hospital_name": "서울아산병원", "address": "서울특별시 송파구 올림픽로43길 88", "district": "송파구", "representative_phone": "02-3010-3114", "emergency_room_phone": "02-3010-3114", "emergency_center_type_name": "권역응급의료센터", "hospital_latitude": 37.5262, "hospital_longitude": 127.1079, "available_er_beds": 7, "available_operating_rooms": 3, "available_general_icu_beds": 5, "available_internal_medicine_icu_beds": 4, "available_surgical_icu_beds": 4, "available_thoracic_icu_beds": 3, "available_neurology_inpatient_beds": 2, "available_neurosurgery_icu_beds": 2, "available_burn_icu_beds": null, "available_trauma_icu_beds": 2, "available_drug_poisoning_icu_beds": 2, "updated_minutes_demo": 33, "route_distance_km": 8.9, "estimated_travel_time_sec": 1440, "traffic_status": "보통", "current_rain_type": "없음", "rain_amount_mm": 0, "hospital_straight_km": 7.8, "map_x": 91, "map_y": 33}, {"hospital_id": "st_mary", "hospital_name": "서울성모병원", "address": "서울특별시 서초구 반포대로 222", "district": "서초구", "representative_phone": "02-2258-6000", "emergency_room_phone": "02-2258-6000", "emergency_center_type_name": "지역응급의료센터", "hospital_latitude": 37.5019, "hospital_longitude": 127.0048, "available_er_beds": 4, "available_operating_rooms": 1, "available_general_icu_beds": 2, "available_internal_medicine_icu_beds": 2, "available_surgical_icu_beds": 1, "available_thoracic_icu_beds": null, "available_neurology_inpatient_beds": 1, "available_neurosurgery_icu_beds": 1, "available_burn_icu_beds": 0, "available_trauma_icu_beds": 0, "available_drug_poisoning_icu_beds": null, "updated_minutes_demo": 68, "route_distance_km": 5.7, "estimated_travel_time_sec": 1080, "traffic_status": "보통", "current_rain_type": "없음", "rain_amount_mm": 0, "hospital_straight_km": 4.9, "map_x": 42, "map_y": 38}];
  const now = Date.now();
  SOURCE_ROWS.forEach(r => r.updated_at = new Date(now - r.updated_minutes_demo * 60000).toISOString());
  const normalize = r => ({
    id:r.hospital_id,
    name:r.hospital_name,
    address:r.address,
    short:`${r.district} · ${r.address.split(' ').slice(-2).join(' ')}`,
    phone:r.emergency_room_phone || r.representative_phone,
    centerType:r.emergency_center_type_name,
    lat:Number(r.hospital_latitude), lng:Number(r.hospital_longitude),
    distanceKm:Number(r.route_distance_km ?? r.hospital_straight_km ?? 999),
    eta:Math.max(1,Math.round(Number(r.estimated_travel_time_sec||0)/60)),
    traffic:r.traffic_status || '정보 없음',
    updatedMinutes:Number(r.updated_minutes_demo ?? Math.max(0,Math.round((now-new Date(r.updated_at))/60000))),
    x:Number(r.map_x||50), y:Number(r.map_y||50),
    beds:{
      er:r.available_er_beds,
      operating:r.available_operating_rooms,
      general:r.available_general_icu_beds,
      internal:r.available_internal_medicine_icu_beds,
      surgical:r.available_surgical_icu_beds,
      thoracic:r.available_thoracic_icu_beds,
      neurology:r.available_neurology_inpatient_beds,
      neurosurgery:r.available_neurosurgery_icu_beds,
      burn:r.available_burn_icu_beds,
      trauma:r.available_trauma_icu_beds,
      poison:r.available_drug_poisoning_icu_beds
    },
    equipment:{CT:null,MRI:null,angiography:null,ventilator:null},
    rainRisk:Number(r.rain_amount_mm||0)>=5?2:Number(r.rain_amount_mm||0)>0?1:0,
    raw:r
  });
  window.APP_DATA = {
    source:{name:'데이터정리_예시_1(2).xlsx',sheet:'전체 데이터 합본',mode:'normalized-schema-adapter',columns:[
      'hospital_id','hospital_name','address','district','emergency_room_phone','emergency_center_type_name','hospital_latitude','hospital_longitude','available_er_beds','available_operating_rooms','updated_at','route_distance_km','estimated_travel_time_sec','traffic_status','current_rain_type','rain_amount_mm'
    ]},
    defaultLocation:{address:'서울특별시 강남구 테헤란로 123',short:'강남구 테헤란로 123',accuracy:'높음',lat:37.5012,lng:127.0396},
    currentWeather:{type:'맑음',label:'맑음',summary:'맑은 날',amountMm:0,rainRisk:0,icon:'clear'},
    symptoms:['흉통','복통','외상','고열','호흡 곤란','두통','마비·의식저하','중독','화상'],
    hospitalRows:SOURCE_ROWS,
    hospitals:SOURCE_ROWS.map(normalize),
    emergencyGuides:[],
    privateAmbulances:[
      {id:'safe-medical-01',name:'세이프 메디컬',vehicle:'특수구급차 12가3456',crew:'응급구조사 동승',arrivalMin:6,fee:85000,tags:['산소 장비','AED','환자 모니터']},
      {id:'blue-cross-02',name:'블루크로스 이송센터',vehicle:'특수구급차 34나7788',crew:'간호인력 동승',arrivalMin:9,fee:78000,tags:['산소 장비','흡인기','휠체어']},
      {id:'care-ambulance-03',name:'케어 앰뷸런스',vehicle:'일반구급차 56다9012',crew:'이송요원 2인',arrivalMin:12,fee:62000,tags:['기본 응급키트','들것','휠체어']}
    ],
    chat:[
      {key:'symptom',q:'가장 불편한 증상을 하나 선택해 주세요.',options:['흉통','복통','외상','호흡 곤란','두통','기타']},
      {key:'onset',q:'증상은 언제부터 시작되었나요?',options:['30분 이내','1~3시간 전','3시간 이상','어제','잘 모르겠어요']},
      {key:'conscious',q:'지금 의식이 또렷한가요?',options:['예','아니요','잘 모르겠어요']},
      {key:'breathing',q:'숨쉬기는 평소와 같나요?',options:['예','아니요','잘 모르겠어요']}
    ]
  };
})();
