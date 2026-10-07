APP_CSS = """
:root {
  --desk: #e8edf1;
  --paper: #fbfaf6;
  --ink: #243744;
  --muted: #697b87;
  --line: #cbd5dc;
  --copper: #9d6544;
}
body { background: var(--desk); color: var(--ink); }
body, .q-field, .q-btn {
  font-family: 'Microsoft YaHei UI', 'PingFang SC', sans-serif;
}
.q-header { background: var(--ink); color: var(--paper); }
.q-header .q-btn { color: var(--paper); }
.q-page-container { padding-top: 68px !important; }
.q-page { min-height: calc(100vh - 68px) !important; }
.q-card { box-shadow: none; border: 1px solid var(--line); }
.q-dialog .q-card { background: var(--paper); }
.q-field--outlined .q-field__control:before { border-color: var(--line); }
.q-btn { text-transform: none; border-radius: 6px; }
.q-btn:focus-visible, a:focus-visible {
  outline: 3px solid var(--copper); outline-offset: 3px;
}
a { color: #456d88; text-underline-offset: 3px; }
.workspace { width: min(1440px, 100%); margin: auto; padding: 32px; }
.brand { font-family: 'Georgia', serif; letter-spacing: .13em; }
.eyebrow { color: var(--copper); font-size: 12px; letter-spacing: .12em; }
.page-title { font-size: 30px; font-weight: 600; letter-spacing: .05em; }
.muted { color: var(--muted); font-size: 14px; }
.paper { background: var(--paper); border-radius: 8px; }
.panel { padding: 24px; }
.record-title { font-size: 20px; font-weight: 600; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-grid > * { min-width: 0; width: 100%; }
.library-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 20px; width: 100%;
}
.stat-line {
  display: flex; gap: 24px; flex-wrap: wrap; border-top: 1px solid var(--line);
  padding-top: 16px; margin-top: 10px;
}
.stat-value { font-size: 28px; font-family: 'Georgia', serif; }
.empty-state { padding: 44px 24px; border: 1px dashed var(--line); }
.reader-grid {
  display: grid; grid-template-columns: 250px minmax(0, 1fr) 280px;
  gap: 20px; align-items: start; width: 100%;
}
.reader-sidebar { position: sticky; top: 92px; }
.reader-paper { padding: 36px 40px; min-height: 55vh; }
.story-event {
  padding: 0 0 24px 20px; margin: 0 0 24px;
  border-left: 2px solid var(--line);
}
.story-event.keeper { border-left-color: var(--copper); }
.story-event .story-text {
  font-family: 'Noto Serif CJK SC', 'Source Han Serif SC', 'SimSun', serif;
  font-size: 17px; line-height: 1.9; letter-spacing: .015em;
}
.event-meta { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.event-detail { font-size: 13px; color: var(--muted); }
.reader-toolbar { display: flex; gap: 10px; flex-wrap: wrap; }
.json-editor textarea {
  font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 13px;
}
.mobile-sidebar-button { display: none; }
.notice { border-left: 3px solid var(--copper); padding: 12px 16px; }
.q-expansion-item__container > .q-item { padding-left: 0; }
@media (max-width: 1150px) {
  .reader-grid { grid-template-columns: 220px minmax(0, 1fr); }
  .map-sidebar { display: none; }
  .mobile-sidebar-button { display: inline-flex; }
}
@media (max-width: 760px) {
  .workspace { padding: 20px 14px; }
  .page-title { font-size: 25px; }
  .form-grid { grid-template-columns: 1fr; }
  .reader-grid { grid-template-columns: minmax(0, 1fr); }
  .character-sidebar { display: none; }
  .reader-paper { padding: 26px 20px; }
  .story-event { padding-left: 14px; }
  .desktop-navigation { display: none; }
  .panel { padding: 20px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation: none !important; transition: none !important;
    scroll-behavior: auto !important;
  }
}
"""
