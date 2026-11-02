// Pendulum Designer: design a movement (from a MAS grade, from target Popovic parameters, or by hand),
// see what the scorer says about it and the waveform the phone should show, play it on the rig, and keep
// a trial log that pairs every run with its StretchExperiment log and its phone recording.
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace Pendulastic
{
    public class MainForm : Form
    {
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
        static readonly string[] ParamNames = { "R2n", "N", "f", "area_ratio", "phi_max_ratio", "omega_max_n", "pt7" };
        // on a flat (horizontal) rig the phone's angle is not trustworthy, but its timing is (bench SOP 2026-09-14)
        static readonly Dictionary<string, bool> FlatRigOk = new Dictionary<string, bool>
            { { "f", true }, { "N", true } };

        readonly string home = Path.GetDirectoryName(Application.ExecutablePath);
        string TrialsCsv { get { return Path.Combine(home, "trials.csv"); } }
        string UrlFile { get { return Path.Combine(home, "variable_url.txt"); } }

        // design
        RadioButton rbMas, rbParams, rbHand;
        ComboBox cbMas;
        readonly Dictionary<string, CheckBox> tick = new Dictionary<string, CheckBox>();
        readonly Dictionary<string, TextBox> aim = new Dictionary<string, TextBox>();
        NumericUpDown nFreq, nDamp, nGain, nThr, nTone, nDepth, nScale;
        Button bDesign, bOpen, bSave;
        // result + plot
        TextBox tResult;
        PlotPanel plot;
        Label lTrial;
        // rig
        TextBox tUrl, tLabel;
        NumericUpDown nMax;
        CheckBox cDry;
        Button bPick, bPlay, bStop;
        Label lStatus;
        // trial log
        DataGridView grid;
        Timer uiTimer;
        SplitContainer split, top, right;
        GroupBox pendingDesign, pendingKnobs;

        DesignResult current;
        string currentDesign = "by hand";
        bool suppressKnobEvents;
        readonly RigPlayer player = new RigPlayer();
        int playingRow = -1;
        bool skipConfirm;

        static readonly string[] Cols = {
            "trial", "label", "pc_start", "duration_s", "design", "rig_file",
            "pred_pt7", "pred_zone", "pred_mas_approx", "pred_R2n", "pred_N", "pred_f", "pred_area_ratio",
            "pred_phi_max_ratio", "pred_omega_max_n", "pred_A0_deg", "peak_rig_deg",
            "encoder_file", "phone_trial", "notes" };
        static readonly string[] Editable = { "label", "encoder_file", "phone_trial", "notes" };

        public MainForm()
        {
            Text = "Pendulum Designer";
            Font = new Font("Segoe UI", 9f);
            Width = 1360; Height = 900; StartPosition = FormStartPosition.CenterScreen; WindowState = FormWindowState.Maximized;
            MinimumSize = new Size(1000, 640);
            BuildUi();
            player.LogDir = Path.Combine(home, "sent");
            if (File.Exists(UrlFile)) tUrl.Text = File.ReadAllText(UrlFile).Trim();
            LoadTrials();
            uiTimer = new Timer { Interval = 33 };
            uiTimer.Tick += delegate { OnUiTick(); };
            uiTimer.Start();
            // splitter positions only stick once the window has its real size
            Shown += delegate
            {
                split.SplitterDistance = Math.Max(300, ClientSize.Height - 210);
                top.SplitterDistance = 405;
                right.SplitterDistance = Math.Max(300, right.Width - 400);
            };
            Shown += delegate
            {
                Redesign();
                // test hook: start a dry-run play by itself (used to check the window and its timing headless)
                string[] a = Environment.GetCommandLineArgs();
                if (Array.IndexOf(a, "--dry-run-play") >= 0) { cDry.Checked = true; Play(); }
                int u = Array.IndexOf(a, "--trial-play");          // test hook: a logged play to this URL, no confirm box
                if (u >= 0 && u + 1 < a.Length) { tUrl.Text = a[u + 1]; tLabel.Text = "hook test"; skipConfirm = true; Play(); }
            };
        }

        // ── layout ─────────────────────────────────────────────────────────────
        void BuildUi()
        {
            split = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal };
            Controls.Add(split);
            top = new SplitContainer { Dock = DockStyle.Fill, FixedPanel = FixedPanel.Panel1 };
            split.Panel1.Controls.Add(top);

            var left = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoScroll = true };
            top.Panel1.Controls.Add(left);

            // 1. what to design
            var g1 = Group("1  What movement?", 380, 300);
            rbMas = new RadioButton { Text = "Aim at a MAS grade:", Left = 10, Top = 22, Width = 150, Checked = true };
            cbMas = new ComboBox { Left = 165, Top = 20, Width = 60, DropDownStyle = ComboBoxStyle.DropDownList };
            cbMas.Items.AddRange(Designer.Grades); cbMas.SelectedItem = "2";
            rbParams = new RadioButton { Text = "Aim at these parameters (tick each one to match):", Left = 10, Top = 50, Width = 360 };
            g1.Controls.AddRange(new Control[] { rbMas, cbMas, rbParams });
            int y = 76;
            var defaults = new Dictionary<string, string> { { "R2n", "1.0" }, { "N", "6.5" }, { "f", "0.88" }, { "area_ratio", "0.09" },
                { "phi_max_ratio", "0.67" }, { "omega_max_n", "9.6" }, { "pt7", "0.40" } };
            foreach (string p in ParamNames)
            {
                var c = new CheckBox { Text = p == "pt7" ? "PT7 score" : p, Left = 30, Top = y, Width = 130 };
                var t = new TextBox { Text = defaults[p], Left = 165, Top = y - 2, Width = 60 };
                g1.Controls.Add(c); g1.Controls.Add(t); tick[p] = c; aim[p] = t; y += 25;
            }
            rbHand = new RadioButton { Text = "By hand (use the knobs below)", Left = 10, Top = y + 2, Width = 300 };
            g1.Controls.Add(rbHand);
            bDesign = new Button { Text = "Design it", Left = 250, Top = 20, Width = 110, Height = 28, Font = new Font(Font, FontStyle.Bold) };
            bDesign.Click += delegate { Redesign(); };
            g1.Controls.Add(bDesign);
            pendingDesign = g1;

            // 2. knobs
            var g2 = Group("2  Model knobs (the design fills these; edit to adjust)", 380, 235);
            y = 22;
            nFreq = Knob(g2, "Swing frequency (Hz)", 0.3m, 4m, 0.9m, 2, 0.05m, ref y);
            nDamp = Knob(g2, "Passive damping (zeta)", 0m, 0.95m, 0.05m, 3, 0.01m, ref y);
            nGain = Knob(g2, "Stretch-reflex gain (1/s)", 0m, 150m, 0m, 1, 1m, ref y);
            nThr = Knob(g2, "Reflex threshold (deg/s)", 0m, 400m, 60m, 0, 5m, ref y);
            nTone = Knob(g2, "Tone: settles above rest (deg)", 0m, 40m, 0m, 1, 1m, ref y);
            nDepth = Knob(g2, "Rest depth below extension (deg)", 20m, 80m, 55m, 1, 1m, ref y);
            nScale = Knob(g2, "Rig amplitude scale", 0.2m, 1.5m, 0.75m, 2, 0.05m, ref y);
            pendingKnobs = g2;

            // 3. rig
            var g3 = Group("Play on the rig: type a trial label, then PLAY", 380, 175);
            g3.Controls.Add(new Label { Text = "Variable:", Left = 10, Top = 24, Width = 60 });
            tUrl = new TextBox { Left = 72, Top = 21, Width = 220 };
            bPick = new Button { Text = "Pick...", Left = 296, Top = 19, Width = 70 };
            bPick.Click += delegate { PickVariable(); };
            g3.Controls.Add(new Label { Text = "Trial label:", Left = 10, Top = 54, Width = 62 });
            tLabel = new TextBox { Left = 72, Top = 51, Width = 294 };
            g3.Controls.Add(new Label { Text = "Max |angle|:", Left = 10, Top = 84, Width = 70 });
            nMax = new NumericUpDown { Left = 82, Top = 81, Width = 60, Minimum = 5, Maximum = 90, Value = 45 };
            cDry = new CheckBox { Text = "Dry run: no motion, no log", Left = 150, Top = 82, Width = 225 };
            bPlay = new Button { Text = "PLAY", Left = 10, Top = 112, Width = 110, Height = 50, Font = new Font(Font.FontFamily, 14f, FontStyle.Bold), BackColor = Color.FromArgb(200, 235, 200) };
            bStop = new Button { Text = "STOP", Left = 128, Top = 112, Width = 110, Height = 50, Font = new Font(Font.FontFamily, 14f, FontStyle.Bold), BackColor = Color.FromArgb(245, 200, 200), Enabled = false };
            bPlay.Click += delegate { Play(); };
            bStop.Click += delegate { player.Stop(); };
            bOpen = new Button { Text = "Open file...", Left = 246, Top = 112, Width = 120 };
            bSave = new Button { Text = "Save file...", Left = 246, Top = 138, Width = 120 };
            bOpen.Click += delegate { OpenFile(); };
            bSave.Click += delegate { SaveFile(); };
            g3.Controls.AddRange(new Control[] { tUrl, bPick, tLabel, nMax, cDry, bPlay, bStop, bOpen, bSave });
            left.Controls.Add(g3); left.Controls.Add(pendingDesign); left.Controls.Add(pendingKnobs);   // PLAY stays on screen

            // right: trial banner, plot, scores
            right = new SplitContainer { Dock = DockStyle.Fill, FixedPanel = FixedPanel.Panel2 };
            top.Panel2.Controls.Add(right);
            lTrial = new Label { Dock = DockStyle.Top, Height = 46, Font = new Font(Font.FontFamily, 20f, FontStyle.Bold), TextAlign = ContentAlignment.MiddleLeft, BackColor = Color.FromArgb(235, 235, 235) };
            plot = new PlotPanel { Dock = DockStyle.Fill };
            right.Panel1.Controls.Add(plot); right.Panel1.Controls.Add(lTrial);
            tResult = new TextBox { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Font = new Font("Consolas", 9.5f), BackColor = Color.White };
            lStatus = new Label { Dock = DockStyle.Bottom, Height = 56, ForeColor = Color.DarkBlue, Font = new Font(Font.FontFamily, 10f) };
            right.Panel2.Controls.Add(tResult); right.Panel2.Controls.Add(lStatus);

            // bottom: trial log
            var gl = new GroupBox { Text = "Trial log  -  saved to trials.csv beside this program after every change. Fill in encoder_file (StretchExperiment's log name) and phone_trial (the phone file's trialN) at the bench.", Dock = DockStyle.Fill };
            split.Panel2.Controls.Add(gl);
            grid = new DataGridView { Dock = DockStyle.Fill, AllowUserToAddRows = false, RowHeadersVisible = false, AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.DisplayedCells, SelectionMode = DataGridViewSelectionMode.CellSelect };
            foreach (string c in Cols)
            {
                int i = grid.Columns.Add(c, c);
                grid.Columns[i].ReadOnly = Array.IndexOf(Editable, c) < 0;
                if (!grid.Columns[i].ReadOnly) grid.Columns[i].DefaultCellStyle.BackColor = Color.FromArgb(255, 255, 220);
            }
            grid.CellEndEdit += delegate { SaveTrials(); };
            var bar = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 32 };
            var bReplay = new Button { Text = "Load selected trial's movement", Width = 200 };
            bReplay.Click += delegate { LoadSelectedTrial(); };
            var bFolder = new Button { Text = "Open folder", Width = 100 };
            bFolder.Click += delegate { System.Diagnostics.Process.Start(home); };
            bar.Controls.Add(bReplay); bar.Controls.Add(bFolder);
            gl.Controls.Add(grid); gl.Controls.Add(bar);

            foreach (var n in new[] { nFreq, nDamp, nGain, nThr, nTone, nDepth, nScale })
                n.ValueChanged += delegate { if (!suppressKnobEvents) { rbHand.Checked = true; Rescore(); } };
        }

        static GroupBox Group(string text, int w, int h) { return new GroupBox { Text = text, Width = w, Height = h }; }

        static NumericUpDown Knob(Control parent, string label, decimal min, decimal max, decimal val, int dec, decimal inc, ref int y)
        {
            parent.Controls.Add(new Label { Text = label, Left = 10, Top = y + 3, Width = 230 });
            var n = new NumericUpDown { Left = 245, Top = y, Width = 80, Minimum = min, Maximum = max, Value = val, DecimalPlaces = dec, Increment = inc };
            parent.Controls.Add(n); y += 29;
            return n;
        }

        // ── design ─────────────────────────────────────────────────────────────
        Knobs KnobsFromUi()
        {
            return new Knobs
            {
                FreqHz = (double)nFreq.Value, Damping = (double)nDamp.Value, ReflexGain = (double)nGain.Value,
                ReflexThresholdDps = (double)nThr.Value, ToneDeg = (double)nTone.Value, DepthDeg = (double)nDepth.Value,
                Scale = (double)nScale.Value
            };
        }

        void KnobsToUi(Knobs k)
        {
            suppressKnobEvents = true;
            try
            {
                Set(nFreq, k.FreqHz); Set(nDamp, k.Damping); Set(nGain, k.ReflexGain); Set(nThr, k.ReflexThresholdDps);
                Set(nTone, k.ToneDeg); Set(nDepth, k.DepthDeg); Set(nScale, k.Scale);
            }
            finally { suppressKnobEvents = false; }
        }

        static void Set(NumericUpDown n, double v)
        {
            decimal d = (decimal)Math.Round(v, n.DecimalPlaces);
            n.Value = Math.Max(n.Minimum, Math.Min(n.Maximum, d));
        }

        void Redesign()
        {
            if (player.Running) return;
            Cursor = Cursors.WaitCursor;
            try
            {
                Knobs basis = KnobsFromUi();
                if (rbMas.Checked)
                {
                    string g = (string)cbMas.SelectedItem;
                    current = Designer.ForGrade(g, basis);
                    currentDesign = "MAS " + g + " preset";
                }
                else if (rbParams.Checked)
                {
                    var targets = new Dictionary<string, double>();
                    var desc = new List<string>();
                    foreach (string p in ParamNames)
                    {
                        if (!tick[p].Checked) continue;
                        double v;
                        if (!double.TryParse(aim[p].Text.Trim(), NumberStyles.Float, Inv, out v)) { MessageBox.Show(this, "Not a number: " + p); return; }
                        targets[p] = v; desc.Add(p + "=" + aim[p].Text.Trim());
                    }
                    current = Designer.ForParams(targets, basis);
                    currentDesign = "aim " + string.Join(" ", desc.ToArray());
                }
                else { current = Designer.Evaluate(basis); currentDesign = "by hand"; current.Note = ""; }
                if (current.Knobs != null) KnobsToUi(current.Knobs);
                Show(current);
            }
            finally { Cursor = Cursors.Default; }
        }

        void Rescore()
        {
            if (player.Running) return;
            current = Designer.Evaluate(KnobsFromUi());
            currentDesign = "by hand"; current.Note = "";
            Show(current);
        }

        void Show(DesignResult r)
        {
            if (r.Movement == null) { tResult.Text = r.Note; return; }
            plot.SetMovement(r.Movement);
            tResult.Text = Report(r);
            lTrial.Text = "  Next: trial " + NextTrialNumber() + (tLabel.Text.Trim().Length > 0 ? "  -  " + tLabel.Text.Trim() : "");
            lTrial.BackColor = Color.FromArgb(235, 235, 235);
        }

        string Report(DesignResult r)
        {
            var m = r.Metrics; var sb = new StringBuilder();
            if (r.Note.Length > 0) sb.AppendLine(r.Note).AppendLine();
            if (!m.Scored) { sb.AppendLine("NOT SCORED: " + m.Reason); return sb.ToString(); }
            sb.AppendLine(string.Format(Inv, "PT7 score   {0}", m.Pt7.HasValue ? m.Pt7.Value.ToString("F3", Inv) : "-"));
            sb.AppendLine("Zone        " + (m.Interpretable ? m.Zone : "not graded"));
            sb.AppendLine("MAS approx  " + (m.Interpretable ? m.Mas : "-") + "   (Popovic's table; rough, see below)");
            if (!m.Interpretable) sb.AppendLine("Refused:    " + m.Reason);
            sb.AppendLine();
            sb.AppendLine("parameter       value   healthy  flat-rig phone");
            foreach (string p in new[] { "R2n", "N", "f", "area_ratio", "phi_max_ratio", "omega_max_n", "A0_deg", "A1_deg" })
            {
                double v; string ref_ = Scoring.HealthyRef(p);
                string vs = m.Values.TryGetValue(p, out v) && !double.IsNaN(v) ? v.ToString("G4", Inv) : "-";
                sb.AppendLine(string.Format(Inv, "{0,-14} {1,7}  {2,7}  {3}", p, vs, ref_, FlatRigOk.ContainsKey(p) ? "comparable" : "not on a flat rig"));
            }
            double peak = 0; foreach (double d in r.Movement.RigDeg) peak = Math.Max(peak, Math.Abs(d));
            sb.AppendLine();
            sb.AppendLine(string.Format(Inv, "Rig: peak {0:F1} deg, {1:F1} s long (release at {2:F2} s).", peak, r.Movement.RigT[r.Movement.RigT.Length - 1], r.Movement.ReleaseT));
            if (peak > (double)nMax.Value) sb.AppendLine("  Beyond Max |angle|: lower the rig amplitude scale to play it.");
            sb.AppendLine();
            sb.AppendLine("MAS here is the PT7-band the score falls in, by Popovic's 2018 table. This project's");
            sb.AppendLine("own data cannot support a grade-by-grade MAS (no leg above MAS 1+); the zone is the");
            sb.AppendLine("verdict the app shows. On the flat rig only f and N from the phone are comparable:");
            sb.AppendLine("its angle reads inverted and drifts in that plane, whatever is played.");
            return sb.ToString();
        }

        // ── files ──────────────────────────────────────────────────────────────
        static void WritePlayerFile(string path, double[] t, double[] sp)
        {
            var sb = new StringBuilder(t.Length * 16);
            for (int i = 0; i < t.Length; i++) sb.Append(t[i].ToString("F4", Inv)).Append(',').Append(sp[i].ToString("F4", Inv)).Append("\r\n");
            File.WriteAllText(path, sb.ToString());
        }

        void SaveFile()
        {
            if (current == null || current.Movement == null) return;
            using (var d = new SaveFileDialog { Filter = "Player file (*.csv)|*.csv", FileName = "design.csv" })
                if (d.ShowDialog(this) == DialogResult.OK) WritePlayerFile(d.FileName, current.Movement.RigT, current.Movement.RigDeg);
        }

        void OpenFile()
        {
            using (var d = new OpenFileDialog { Filter = "Player file (*.csv)|*.csv" })
            {
                if (d.ShowDialog(this) != DialogResult.OK) return;
                LoadMovementFile(d.FileName, "file " + Path.GetFileName(d.FileName));
            }
        }

        void LoadMovementFile(string path, string design)
        {
            var t = new List<double>(); var sp = new List<double>();
            foreach (string line in File.ReadAllLines(path))
            {
                string s = line.Trim(); if (s.Length == 0) continue;
                if (char.IsLetter(s[0])) { MessageBox.Show(this, "This file has a header row; use the player\\ file, not commands\\."); return; }
                string[] p = s.Split(',');
                t.Add(double.Parse(p[0], Inv)); sp.Add(double.Parse(p[1], Inv));
            }
            var m = PendulumModel.FromRigFile(t.ToArray(), sp.ToArray());
            current = new DesignResult { Movement = m, Metrics = Scoring.Score(m), Note = "Loaded " + path };
            currentDesign = design;
            Show(current);
        }

        // ── rig ────────────────────────────────────────────────────────────────
        void PickVariable()
        {
            try
            {
                string u = RigPlayer.PickUrl();
                if (u != null) { tUrl.Text = u; File.WriteAllText(UrlFile, u); }
            }
            catch (Exception ex) { MessageBox.Show(this, ex.Message); }
        }

        void Play()
        {
            if (current == null || current.Movement == null || player.Running) return;
            bool dry = cDry.Checked;
            if (!dry && tUrl.Text.Trim().Length == 0) { MessageBox.Show(this, "Pick the setpoint variable first (Pick...)."); return; }
            var mv = current.Movement;
            string why = RigPlayer.Check(mv.RigT, mv.RigDeg, (double)nMax.Value);
            if (why != null) { MessageBox.Show(this, "Not played: " + why + "."); return; }
            if (!dry)
            {
                if (!skipConfirm && MessageBox.Show(this, "Arm at 0?  Phone recording?  StretchExperiment log running?\n\nPlay trial " + NextTrialNumber() + " now?",
                                    "Pendulum Designer", MessageBoxButtons.OKCancel, MessageBoxIcon.Question) != DialogResult.OK) return;
                File.WriteAllText(UrlFile, tUrl.Text.Trim());
                playingRow = AddTrial(mv);
            }
            player.Url = tUrl.Text.Trim(); player.MaxDeg = (double)nMax.Value; player.DryRun = dry;
            try { player.Start(mv.RigT, mv.RigDeg); }
            catch (Exception ex) { MessageBox.Show(this, ex.Message); return; }
            bPlay.Enabled = false; bStop.Enabled = true; bDesign.Enabled = false;
            lTrial.BackColor = dry ? Color.FromArgb(255, 240, 200) : Color.FromArgb(190, 240, 190);
            lTrial.Text = dry ? "  DRY RUN  (not a trial)" : "  TRIAL " + grid.Rows[playingRow].Cells["trial"].Value + "  -  " + grid.Rows[playingRow].Cells["label"].Value;
        }

        void OnUiTick()
        {
            if (player.Running)
            {
                double ft = player.FileTimeS;
                plot.SetCursor(ft);
                lStatus.Text = player.Status + (ft >= 0 ? string.Format(Inv, "   {0:F1} s   setpoint {1:F2} deg", ft, player.LastSentDeg) : "");
            }
            else if (!bPlay.Enabled)
            {
                plot.SetCursor(-1);
                bPlay.Enabled = true; bStop.Enabled = false; bDesign.Enabled = true;
                string timing = string.Format(Inv, "updates every {0:F0} ms (95th pct {1:F0}, worst {2:F0})", player.MedianGapMs, player.P95GapMs, player.WorstGapMs);
                lStatus.Text = player.Status + "   " + timing + (player.P95GapMs > 25 ? "  SLOW: close other programs" : "");
                if (playingRow >= 0)
                {
                    var c = grid.Rows[playingRow].Cells["notes"];
                    if (player.Status.StartsWith("STOPPED") || player.Status.StartsWith("stopped"))
                        c.Value = (Convert.ToString(c.Value) + " " + player.Status).Trim();
                    SaveTrials();
                    playingRow = -1;
                }
                lTrial.BackColor = Color.FromArgb(235, 235, 235);
                lTrial.Text = "  Last: " + lTrial.Text.Trim() + "    Next: trial " + NextTrialNumber();
            }
        }

        // ── trial log ──────────────────────────────────────────────────────────
        int NextTrialNumber()
        {
            int n = 0;
            foreach (DataGridViewRow r in grid.Rows) { int v; if (int.TryParse(Convert.ToString(r.Cells["trial"].Value), out v)) n = Math.Max(n, v); }
            return n + 1;
        }

        int AddTrial(Movement mv)
        {
            int no = NextTrialNumber();
            string label = tLabel.Text.Trim().Length > 0 ? tLabel.Text.Trim() : currentDesign;
            string dir = Path.Combine(home, "trial_files"); Directory.CreateDirectory(dir);
            string fname = string.Format(Inv, "T{0:D3}.csv", no);
            WritePlayerFile(Path.Combine(dir, fname), mv.RigT, mv.RigDeg);
            var m = current.Metrics; double peak = 0; foreach (double d in mv.RigDeg) peak = Math.Max(peak, Math.Abs(d));
            int i = grid.Rows.Add();
            var c = grid.Rows[i].Cells;
            c["trial"].Value = no.ToString(Inv); c["label"].Value = label;
            c["pc_start"].Value = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", Inv);
            c["duration_s"].Value = mv.RigT[mv.RigT.Length - 1].ToString("F1", Inv);
            c["design"].Value = currentDesign; c["rig_file"].Value = "trial_files\\" + fname;
            c["pred_pt7"].Value = m.Pt7.HasValue ? m.Pt7.Value.ToString("F3", Inv) : "";
            c["pred_zone"].Value = m.Interpretable ? m.Zone : "not graded";
            c["pred_mas_approx"].Value = m.Interpretable ? m.Mas : "";
            foreach (string p in new[] { "R2n", "N", "f", "area_ratio", "phi_max_ratio", "omega_max_n", "A0_deg" })
            {
                double v; c["pred_" + p].Value = m.Values.TryGetValue(p, out v) && !double.IsNaN(v) ? v.ToString("G5", Inv) : "";
            }
            c["peak_rig_deg"].Value = peak.ToString("F1", Inv);
            c["encoder_file"].Value = ""; c["phone_trial"].Value = ""; c["notes"].Value = "";
            SaveTrials();
            grid.FirstDisplayedScrollingRowIndex = i;
            return i;
        }

        void LoadSelectedTrial()
        {
            if (grid.CurrentCell == null) return;
            var r = grid.Rows[grid.CurrentCell.RowIndex];
            string f = Path.Combine(home, Convert.ToString(r.Cells["rig_file"].Value));
            if (!File.Exists(f)) { MessageBox.Show(this, "Missing " + f); return; }
            tLabel.Text = Convert.ToString(r.Cells["label"].Value) + " (repeat)";
            LoadMovementFile(f, "repeat of trial " + r.Cells["trial"].Value);
        }

        static string Csv(object v)
        {
            string s = Convert.ToString(v, Inv) ?? "";
            return s.IndexOfAny(new[] { ',', '"', '\n', '\r' }) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;
        }

        void SaveTrials()
        {
            var sb = new StringBuilder();
            sb.Append(string.Join(",", Cols)).Append("\r\n");
            foreach (DataGridViewRow r in grid.Rows)
            {
                var parts = new string[Cols.Length];
                for (int j = 0; j < Cols.Length; j++) parts[j] = Csv(r.Cells[Cols[j]].Value);
                sb.Append(string.Join(",", parts)).Append("\r\n");
            }
            try { File.WriteAllText(TrialsCsv, sb.ToString()); }
            catch (Exception ex) { lStatus.Text = "Could not save trials.csv (open in Excel?): " + ex.Message; }
        }

        void LoadTrials()
        {
            if (!File.Exists(TrialsCsv)) return;
            string[] lines = File.ReadAllLines(TrialsCsv);
            if (lines.Length == 0) return;
            string[] head = SplitCsv(lines[0]);
            for (int k = 1; k < lines.Length; k++)
            {
                if (lines[k].Trim().Length == 0) continue;
                string[] v = SplitCsv(lines[k]);
                int i = grid.Rows.Add();
                for (int j = 0; j < head.Length && j < v.Length; j++)
                    if (grid.Columns.Contains(head[j])) grid.Rows[i].Cells[head[j]].Value = v[j];
            }
        }

        static string[] SplitCsv(string line)
        {
            var o = new List<string>(); var cur = new StringBuilder(); bool q = false;
            for (int i = 0; i < line.Length; i++)
            {
                char ch = line[i];
                if (q) { if (ch == '"') { if (i + 1 < line.Length && line[i + 1] == '"') { cur.Append('"'); i++; } else q = false; } else cur.Append(ch); }
                else if (ch == '"') q = true;
                else if (ch == ',') { o.Add(cur.ToString()); cur.Length = 0; }
                else cur.Append(ch);
            }
            o.Add(cur.ToString());
            return o.ToArray();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (player.Running)
            {
                MessageBox.Show(this, "A movement is playing. Press STOP and wait for it to return to 0 first.");
                e.Cancel = true; return;
            }
            grid.EndEdit(); SaveTrials();
            base.OnFormClosing(e);
        }
    }

    /// <summary>Two stacked plots: what the rig is told (whole file) and what the phone should read (from release).</summary>
    public class PlotPanel : Panel
    {
        Movement m; double cursorS = -1;
        public PlotPanel() { DoubleBuffered = true; BackColor = Color.White; ResizeRedraw = true; }
        public void SetMovement(Movement mv) { m = mv; Invalidate(); }
        public void SetCursor(double fileS) { if (Math.Abs(fileS - cursorS) > 1e-3) { cursorS = fileS; Invalidate(); } }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (m == null) return;
            var g = e.Graphics; g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            int h = ClientSize.Height, w = ClientSize.Width;
            var r1 = new Rectangle(60, 22, w - 80, h / 2 - 50);
            var r2 = new Rectangle(60, h / 2 + 22, w - 80, h / 2 - 50);
            Draw(g, r1, m.RigT, m.RigDeg, 0, m.RigT[m.RigT.Length - 1], "Rig arm (deg from StretchExperiment's zero) - the whole file", Color.FromArgb(30, 90, 180), cursorS);
            // phone panel, on the file's clock: release in the rig file = release in the knee trace
            double off = m.ReleaseT - m.KneeReleaseT;
            var kt = new double[m.KneeT.Length];
            for (int i = 0; i < kt.Length; i++) kt[i] = m.KneeT[i] + off;
            Draw(g, r2, kt, m.KneeDeg, kt[0], kt[kt.Length - 1], "Phone knee angle (180 = full extension) - what the app should show from release", Color.FromArgb(200, 80, 30), cursorS);
        }

        static void Draw(Graphics g, Rectangle r, double[] t, double[] y, double t0, double t1, string title, Color col, double cur)
        {
            double lo = double.MaxValue, hi = double.MinValue;
            for (int i = 0; i < y.Length; i++) { if (t[i] < t0 || t[i] > t1) continue; lo = Math.Min(lo, y[i]); hi = Math.Max(hi, y[i]); }
            double pad = Math.Max(1, (hi - lo) * 0.08); lo -= pad; hi += pad;
            using (var f = new Font("Segoe UI", 8.5f))
            using (var grid = new Pen(Color.FromArgb(230, 230, 230)))
            using (var axis = new Pen(Color.Gray))
            using (var line = new Pen(col, 1.6f))
            {
                g.DrawString(title, new Font(f, FontStyle.Bold), Brushes.Black, r.Left, r.Top - 18);
                for (int k = 0; k <= 4; k++)
                {
                    double v = lo + (hi - lo) * k / 4; int yy = (int)(r.Bottom - (v - lo) / (hi - lo) * r.Height);
                    g.DrawLine(grid, r.Left, yy, r.Right, yy);
                    g.DrawString(v.ToString("F0", CultureInfo.InvariantCulture), f, Brushes.DimGray, r.Left - 34, yy - 7);
                }
                for (double s = Math.Ceiling(t0); s <= t1; s += (t1 - t0) > 30 ? 5 : 1)
                {
                    int xx = (int)(r.Left + (s - t0) / (t1 - t0) * r.Width);
                    g.DrawLine(grid, xx, r.Top, xx, r.Bottom);
                    g.DrawString(s.ToString("F0", CultureInfo.InvariantCulture), f, Brushes.DimGray, xx - 6, r.Bottom + 2);
                }
                g.DrawRectangle(axis, r);
                int step = Math.Max(1, y.Length / (r.Width * 2));
                var pts = new List<PointF>();
                for (int i = 0; i < y.Length; i += step)
                {
                    if (t[i] < t0 || t[i] > t1) continue;
                    pts.Add(new PointF((float)(r.Left + (t[i] - t0) / (t1 - t0) * r.Width), (float)(r.Bottom - (y[i] - lo) / (hi - lo) * r.Height)));
                }
                if (pts.Count > 1) g.DrawLines(line, pts.ToArray());
                if (cur >= t0 && cur <= t1)
                {
                    int xx = (int)(r.Left + (cur - t0) / (t1 - t0) * r.Width);
                    using (var cp = new Pen(Color.FromArgb(220, 30, 30), 2f)) g.DrawLine(cp, xx, r.Top, xx, r.Bottom);
                }
            }
        }
    }

    static class Program
    {
        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.Run(new MainForm());
        }
    }
}
