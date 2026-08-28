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

  // 2단계 — 한국 이름 + 발음
  h += '<div class="cr-step">' +
    stepLabel(2, 'Your Korean name') +
    chips(r.korean.syllables, 'var(--ink)') +
    '<div class="cr-fullname">Your name: <b>' + esc(r.korean.hangul) +
    '</b> (' + esc(r.korean.romanized) + ')</div>' +
    '</div>';

  // 3단계 — 음절 매칭 (색 구분)
  h += '<div class="cr-step">' +
    stepLabel(3, 'How we matched the sounds') +
    '<div class="cr-intro">We carried the most distinctive syllables of &ldquo;' +
    esc(r.english_name) + '&rdquo; (' + distinct + ') into a name that reads naturally in Korean:</div>';

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
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#1D9E75"></span>strong &mdash; nearly the same sound</span>' +
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#639922"></span>partial &mdash; one sound in common</span>' +
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#BA7517"></span>soft &mdash; only the consonant or vowel</span>' +
    '<span class="cr-legend-item"><span class="cr-dot" style="background:#D85A30"></span>loose &mdash; only a hint in common</span>' +
    '</div>';

  h += '</div>';

  // 4단계 — Q3/Q4 안내
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
