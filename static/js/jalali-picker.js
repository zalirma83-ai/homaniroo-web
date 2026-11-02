/* Local, dependency-free Persian follow-up picker. Stored date semantics stay server-owned. */
(() => {
  'use strict';
  const fields = [...document.querySelectorAll('input[data-jalali-picker="datetime"], input[data-jalali-picker="date"]')];
  if (!fields.length || !window.Intl) return;
  const fa = new Intl.NumberFormat('fa-IR', {useGrouping:false});
  const months = ['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'];
  const pad = n => String(n).padStart(2,'0');
  const ascii = s => s.replace(/[۰-۹٠-٩]/g,c => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(c)>=0 ? '۰۱۲۳۴۵۶۷۸۹'.indexOf(c) : '٠١٢٣٤٥٦٧٨٩'.indexOf(c)));
  // Match the server's jdatetime 33-year leap rule, not browser-specific calendar tables.
  const leap = year => [1,5,9,13,17,22,26,30].includes(year % 33);
  const epoch = Date.UTC(2026,2,21,12); // 1405/01/01, same anchor as the server.
  const starts = new Map([[1405,epoch]]);
  function yearStart(year) {
    if (starts.has(year)) return starts.get(year);
    let days=0;
    for(let y=Math.min(year,1405);y<Math.max(year,1405);y++)days+=leap(y)?366:365;
    const value=epoch+(year>=1405?days:-days)*86400000;starts.set(year,value);return value;
  }
  function parts(date) {
    let year=date.getUTCFullYear()-621;
    while(date.getTime()<yearStart(year))year--;
    while(date.getTime()>=yearStart(year+1))year++;
    const offset=Math.floor((date.getTime()-yearStart(year))/86400000);
    return offset<186 ? {year,month:Math.floor(offset/31)+1,day:offset%31+1}
      : {year,month:Math.floor((offset-186)/30)+7,day:(offset-186)%30+1};
  }
  const shift = (d,n) => new Date(d.getTime()+n*86400000);
  function gregorian(y,m,d) {
    if (!Number.isInteger(y)||y<1200||y>1600||m<1||m>12||d<1||d>31) return null;
    const max=m<=6?31:m<=11?30:leap(y)?30:29;
    if(d>max)return null;
    const offset=m<=6?(m-1)*31:186+(m-7)*30;
    return new Date(yearStart(y)+(offset+d-1)*86400000);
  }
  function today() {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Tehran',year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));
    return {date:new Date(Date.UTC(+p.year,+p.month-1,+p.day,12)),hour:+p.hour,minute:+p.minute};
  }
  function parse(value, mode) {
    const pattern=mode==='date' ? /^(\d{4})\/(\d{1,2})\/(\d{1,2})$/ : /^(\d{4})\/(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2})$/;
    const m=ascii(value.trim()).match(pattern);
    if (!m||+m[4]>23||+m[5]>59) return null;
    const date=gregorian(+m[1],+m[2],+m[3]);
    return date ? {date,hour:+(m[4]||0),minute:+(m[5]||0)} : null;
  }
  const popup=document.createElement('div');
  popup.id='jalali-followup-picker';popup.className='jalali-picker';popup.hidden=true;popup.dir='rtl';
  popup.setAttribute('role','dialog');popup.setAttribute('aria-label','انتخاب تاریخ و ساعت');document.body.append(popup);
  let active=null, selected=null, view=null, hour=0, minute=0;
  function button(text,label,fn) {const b=document.createElement('button');b.type='button';b.textContent=text;if(label)b.setAttribute('aria-label',label);b.addEventListener('click',fn);return b;}
  function close(restore=true) {if(!active)return;const old=active;active.setAttribute('aria-expanded','false');active=null;popup.hidden=true;if(restore)old.focus();}
  function position() {
    if(!active)return;
    const r=active.getBoundingClientRect(),w=popup.offsetWidth,h=popup.offsetHeight;
    const x=Math.min(Math.max(12,r.right-w),Math.max(12,innerWidth-w-12));
    const y=r.bottom+8+h<=innerHeight-12 ? r.bottom+8 : Math.max(12,r.top-h-8);
    popup.style.left=x+'px';popup.style.top=y+'px';
  }
  function commit(value) {const field=active;field.value=value;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));close();}
  function changeMonth(delta) {
    const p=parts(view);let y=p.year,m=p.month+delta;if(m===0){m=12;y--;}if(m===13){m=1;y++;}
    const next=gregorian(y,m,1);if(next){view=next;render();}
  }
  function render(focusDay=false) {
    popup.replaceChildren();
    const p=parts(view),first=gregorian(p.year,p.month,1),now=today(),nowp=parts(now.date);
    const heading=document.createElement('div');heading.className='jalali-picker-heading';
    const title=document.createElement('strong');title.textContent=active.dataset.jalaliPicker==='date'?'انتخاب تاریخ':'انتخاب تاریخ و ساعت';heading.append(title,button('×','بستن تقویم',()=>close()));popup.append(heading);
    const nav=document.createElement('div');nav.className='jalali-picker-nav';
    const prev=button('‹','ماه قبل',()=>changeMonth(-1)),next=button('›','ماه بعد',()=>changeMonth(1));
    const month=document.createElement('select');month.setAttribute('aria-label','ماه');
    months.forEach((name,i)=>month.add(new Option(name,String(i+1),false,i+1===p.month)));
    month.addEventListener('change',()=>{view=gregorian(p.year,+month.value,1);render();});
    const year=document.createElement('select');year.setAttribute('aria-label','سال');
    for(let y=1200;y<=1600;y++)year.add(new Option(fa.format(y),String(y),false,y===p.year));
    year.addEventListener('change',()=>{view=gregorian(+year.value,p.month,1);render();});
    nav.append(prev,month,year,next);popup.append(nav);
    const grid=document.createElement('div');grid.className='jalali-picker-grid';grid.setAttribute('role','group');grid.setAttribute('aria-label',months[p.month-1]+' '+fa.format(p.year));
    ['ش','ی','د','س','چ','پ','ج'].forEach((name,i)=>{const s=document.createElement('span');s.textContent=name;s.className='jalali-weekday';s.setAttribute('aria-label',['شنبه','یکشنبه','دوشنبه','سه‌شنبه','چهارشنبه','پنجشنبه','جمعه'][i]);grid.append(s);});
    for(let i=0;i<(first.getUTCDay()+1)%7;i++){const blank=document.createElement('span');blank.setAttribute('aria-hidden','true');grid.append(blank);}
    for(let d=1;d<=31;d++) {
      const g=shift(first,d-1);if(parts(g).month!==p.month)break;
      const chosen=g.getTime()===selected.getTime();
      const b=button(fa.format(d),fa.format(d)+' '+months[p.month-1]+' '+fa.format(p.year),()=>{selected=g;render(true);});
      b.dataset.day=String(d);b.setAttribute('aria-pressed',String(chosen));b.tabIndex=chosen?0:-1;
      if(chosen)b.classList.add('is-selected');
      if(p.year===nowp.year&&p.month===nowp.month&&d===nowp.day)b.classList.add('is-today');
      b.addEventListener('keydown',e=>{
        const delta={ArrowLeft:1,ArrowRight:-1,ArrowDown:7,ArrowUp:-7}[e.key];
        if(delta!==undefined){e.preventDefault();const dest=shift(g,delta),dp=parts(dest);if(dp.year<1200||dp.year>1600)return;selected=dest;view=gregorian(dp.year,dp.month,1);render(true);}
      });grid.append(b);
    }
    if(!grid.querySelector('button[tabindex="0"]'))grid.querySelector('button').tabIndex=0;
    popup.append(grid);
    const time=document.createElement('div');time.className='jalali-picker-time';
    function timeSelect(label,max,value,setter) {const wrap=document.createElement('label');wrap.textContent=label;const s=document.createElement('select');s.setAttribute('aria-label',label);for(let n=0;n<max;n++)s.add(new Option(fa.format(n).padStart(2,'۰'),String(n),false,n===value));s.addEventListener('change',()=>setter(+s.value));wrap.append(s);return wrap;}
    time.append(timeSelect('ساعت',24,hour,v=>hour=v),timeSelect('دقیقه',60,minute,v=>minute=v));if(active.dataset.jalaliPicker!=='date')popup.append(time);
    const foot=document.createElement('div');foot.className='jalali-picker-actions';
    foot.append(button('امروز',null,()=>{selected=now.date;const n=parts(selected);view=gregorian(n.year,n.month,1);render(true);}),button('پاک‌کردن',null,()=>commit('')));
    const apply=button('انتخاب',null,()=>{const q=parts(selected);commit(q.year+'/'+pad(q.month)+'/'+pad(q.day)+(active.dataset.jalaliPicker==='date'?'':' '+pad(hour)+':'+pad(minute)));});apply.className='jalali-picker-apply';foot.append(apply);popup.append(foot);
    position();if(focusDay)(grid.querySelector('.is-selected')||grid.querySelector('button')).focus();
  }
  function open(field) {
    if(active===field&&!popup.hidden)return;
    if(active)active.setAttribute('aria-expanded','false');active=field;
    const initial=parse(field.value,field.dataset.jalaliPicker)||today();selected=initial.date;hour=initial.hour;minute=initial.minute;
    const p=parts(selected);view=gregorian(p.year,p.month,1);popup.hidden=false;popup.setAttribute('aria-label',field.dataset.jalaliPicker==='date'?'انتخاب تاریخ':'انتخاب تاریخ و ساعت');field.setAttribute('aria-expanded','true');render();
  }
  fields.forEach(field=>{
    field.setAttribute('aria-haspopup','dialog');field.setAttribute('aria-controls',popup.id);field.setAttribute('aria-expanded','false');field.autocomplete='off';
    field.addEventListener('click',()=>open(field));
    field.addEventListener('keydown',e=>{if((e.altKey&&e.key==='ArrowDown')||e.key==='F4'){e.preventDefault();open(field);popup.querySelector('button').focus();}});
  });
  document.addEventListener('keydown',e=>{if(active&&e.key==='Escape'){e.preventDefault();close();}});
  document.addEventListener('pointerdown',e=>{if(active&&!popup.contains(e.target)&&e.target!==active)close(false);});
  document.addEventListener('focusin',e=>{if(active&&!popup.contains(e.target)&&e.target!==active)close(false);});
  addEventListener('resize',position);addEventListener('scroll',position,true);
})();
