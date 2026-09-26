/* ==========================================================================
   변환 이유 렌더러
   renderConversionReason(container, reasonData)
     reasonData = app.py의 build_reason() 결과 (result.html에서 window.REASON로 주입)
   ========================================================================== */
function renderConversionReason(container, r) {
  if (!container || !r) return;
  var esc = function (s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  };

  function chips(syllables, romColor) {
    return '<div class="cr-chips">' + syllables.map(function (s) {
      return '<div class="cr-chip">' +
        '<span class="cr-chip-ch">' + esc(s.char) + '</span>' +
        '<span class="cr-chip-rom" style="color:' + romColor + '">' + esc(s.rom) + '</span>' +
        '<span class="cr-chip-hint">' + esc(s.hint) + '</span>' +
        '</div>';
    }).join('') + '</div>';
  }

  function glyph(ch, rom, col) {
    return '<div class="cr-glyph">' +
      '<span class="cr-glyph-ch" style="color:' + col + '">' + esc(ch) + '</span>' +
      '<span class="cr-glyph-rom" style="color:' + col + '">' + esc(rom) + '</span>' +
      '</div>';
  }

  function stepLabel(n, txt) {
    return '<div class="cr-step-label"><span class="cr-step-num">' + n + '</span>' + txt + '</div>';
  }

  var distinct = r.matches.map(function (m) {
    return '<b>' + esc(m.src) + '</b> (' + esc(m.src_rom) + ')';
  }).join(', ');

  var h = '';

  // 1단계 — 음차 + 발음
  h += '<div class="cr-step">' +
    stepLabel(1, 'How &ldquo;' + esc(r.english_name) + '&rdquo; sounds in Korean') +
    chips(r.translit.syllables, 'var(--ink-soft)') +
    '<div class="cr-fullname">Written in Hangul: <b>' + esc(r.translit.hangul) +
    '</b> (' + esc(r.translit.romanized) + ')</div>' +
    '</div>';

  // 2단계 — 한국 이름 + 발음 + 어떤 음절을 가져왔는지 한 문장
  // 매칭이 하나면 단수 (conversion_reason.py 의 block3 과 같은 규칙)
  var noun = r.matches.length === 1 ? 'syllable' : 'syllables';
  h += '<div class="cr-step">' +
    stepLabel(2, 'Your Korean name') +
    chips(r.korean.syllables, 'var(--ink)') +
    '<div class="cr-fullname">Your name: <b>' + esc(r.korean.hangul) +
    '</b> (' + esc(r.korean.romanized) + ')</div>' +
    '<div class="cr-intro">We carried the most distinctive ' + noun + ' of &ldquo;' +
    esc(r.english_name) + '&rdquo; (' + distinct + ') into a name that reads naturally in Korean.</div>' +
    '</div>';

  // 3단계 — 음절 매칭 (색 구분). 설명 문장은 2단계로 옮겼고 여기선 바로 매칭 상자.
  h += '<div class="cr-step">' +
    stepLabel(3, 'How we matched the sounds');

  h += r.matches.map(function (m) {
    var st = m.style;
    return '<div class="cr-match" style="background:' + st.bg + '">' +
      glyph(m.src, m.src_rom, st.tx) +
      '<span class="cr-arrow" style="color:' + st.ar + '">&rarr;</span>' +
      glyph(m.tgt, m.tgt_rom, st.tx) +
      '<span class="cr-match-text" style="color:' + st.tx + '">the <b>' + esc(m.src) +
      '</b> (' + esc(m.src_rom) + ') sound ' + esc(m.phrase) + ' <b>' + esc(m.tgt) +
      '</b> (' + esc(m.tgt_rom) + ')</span>' +
      '</div>';
  }).join('');

  // 범례 — 색깔 있는 매칭 박스 바로 아래 (같은 단계 안)
  h += '<div class="cr-legend">' +
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#1D9E75"></span>strong &mdash; the same or nearly the same sound</span>' +
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#639922"></span>partial &mdash; one sound in common</span>' +
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#BA7517"></span>soft &mdash; only the consonant or vowel</span>' +
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#D85A30"></span>loose &mdash; only a hint in common</span>' +
    // 공통 소리가 전혀 없어 다른 음절로 바꾼 경우에만 한 줄 더 (match_phrasing.py 의 'replaced')
    (r.matches.some(function (m) { return m.style.label === 'replaced'; })
      ? '<span class="cr-legend-item"><span class="cr-dot" style="background:#8A7E6E"></span>replaced &mdash; no natural match, new syllable</span>'
      : '') +
    '</div>';

  h += '</div>';

  // 4단계 — 가깝게 못 맞췄을 때의 안내 (build_reason 의 note)
  if (r.note) {
    h += '<div class="cr-note">' +
      '<div class="cr-note-head">A note on this name</div>' +
      '<div class="cr-note-body">' + esc(r.note) + '</div>' +
      '</div>';
  }

  // 순우리말 이름 안내 — 한자가 없는 것이 누락이 아님을 알린다
  if (r.native_note) {
    h += '<div class="cr-note cr-note-native">' +
      '<div class="cr-note-head">A native Korean name</div>' +
      '<div class="cr-note-body">' +
      r.native_note.split('\n\n').map(function (p) {
        return '<p>' + esc(p) + '</p>';
      }).join('') +
      '</div>' +
      '</div>';
  }

  container.innerHTML = h;
}
