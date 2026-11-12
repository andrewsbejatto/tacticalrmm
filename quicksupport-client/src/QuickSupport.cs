// Tactical RMM - Quick Support
//
// Generic, reusable executable for remote support of computers that do not
// have the Tactical RMM / MeshCentral agent installed.
//
// Every time it runs it:
//   1. opens a session on the Tactical RMM server and receives a support code
//   2. downloads the MeshCentral agent and runs it in temporary mode
//      ("connect" mode, nothing is installed)
//   3. shows the code; the technician types it in Tactical RMM to connect
//   4. when the window is closed the agent is stopped and all files removed
//
// The server settings (api url + token) are appended to the end of this
// executable by the Tactical RMM server when it is downloaded from the
// dashboard (Quick Support > Download). For testing they can also be passed
// on the command line: --api=https://api.example.com --token=xxxx

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("Tactical RMM Quick Support")]
[assembly: AssemblyProduct("Tactical RMM Quick Support")]
[assembly: AssemblyDescription("Remote support without installing an agent")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace TacticalRMM.QuickSupport
{
    internal static class Program
    {
        public const string Version = "1.0.0";

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetDefaultDllDirectories(uint directoryFlags);
        private const uint LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800;

        [STAThread]
        private static void Main(string[] args)
        {
            // this file usually runs from the Downloads folder: never load DLLs
            // from the application folder (dll planting), only from System32
            try { SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32); } catch (Exception) { }

            // restarted as administrator: wait for the previous instance to close
            foreach (var arg in args)
            {
                int pid;
                if (arg.StartsWith("--wait=") && int.TryParse(arg.Substring(7), out pid))
                {
                    try { Process.GetProcessById(pid).WaitForExit(30000); } catch (Exception) { }
                }
            }

            bool createdNew;
            using (var mutex = new Mutex(true, "Global\\TRMMQuickSupport", out createdNew))
            {
                if (!createdNew)
                {
                    MessageBox.Show(Strings.AlreadyRunning, Strings.Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);

                ClientConfig config;
                string error;
                if (!ClientConfig.TryLoad(args, out config, out error))
                {
                    MessageBox.Show(error, Strings.Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                Application.Run(new MainForm(config));
            }
        }
    }

    // ---------------------------------------------------------------------
    // Localized strings (pt-BR when Windows is in Portuguese, else English)
    // ---------------------------------------------------------------------
    internal static class Strings
    {
        private static readonly bool Pt = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "pt";

        public static string Title { get { return Pt ? "Suporte Remoto" : "Remote Support"; } }
        public static string YourCode { get { return Pt ? "Informe este código ao técnico:" : "Give this code to the technician:"; } }
        public static string Connecting { get { return Pt ? "Conectando ao servidor..." : "Connecting to the server..."; } }
        public static string Downloading { get { return Pt ? "Aguarde: baixando e preparando os arquivos do suporte..." : "Please wait: downloading and preparing the support files..."; } }
        public static string Waiting { get { return Pt ? "Pronto! Aguardando o técnico se conectar." : "Ready! Waiting for the technician to connect."; } }
        public static string ConnectedBy { get { return Pt ? "Técnico conectado: {0}" : "Technician connected: {0}"; } }
        public static string Connected { get { return Pt ? "Técnico conectado." : "Technician connected."; } }
        public static string Ended { get { return Pt ? "A sessão de suporte foi encerrada." : "The support session has ended."; } }
        public static string StillPreparing { get { return Pt ? "Aguarde: ainda conectando ao servidor de suporte..." : "Please wait: still connecting to the support server..."; } }
        public static string Retrying { get { return Pt ? "Sem conexão com o servidor, tentando novamente..." : "Unable to reach the server, retrying..."; } }
        public static string Failed { get { return Pt ? "Não foi possível iniciar o suporte remoto:\n{0}" : "Unable to start remote support:\n{0}"; } }
        public static string CopyCode { get { return Pt ? "Copiar código" : "Copy code"; } }
        public static string Copied { get { return Pt ? "Código copiado!" : "Code copied!"; } }
        public static string EndSupport { get { return Pt ? "Encerrar suporte" : "End support"; } }
        public static string RunAsAdmin { get { return Pt ? "Executar como administrador" : "Run as administrator"; } }
        public static string RunningAsAdmin { get { return Pt ? "Executando como administrador" : "Running as administrator"; } }
        public static string Info { get { return Pt ? "Enquanto esta janela estiver aberta o técnico poderá acessar este computador. Feche-a para encerrar o acesso. Nada é instalado." : "While this window is open the technician can access this computer. Close it to end the access. Nothing is installed."; } }
        public static string ConfirmClose { get { return Pt ? "Encerrar o suporte remoto? O técnico será desconectado." : "End remote support? The technician will be disconnected."; } }
        public static string BadAgent { get { return Pt ? "O arquivo de acesso remoto baixado não confere com o esperado (possível adulteração). Tente novamente ou fale com o suporte." : "The downloaded remote access file does not match the expected one (possible tampering). Try again or contact support."; } }
        public static string AlreadyRunning { get { return Pt ? "O suporte remoto já está em execução." : "Remote support is already running."; } }
        public static string InstallPermanent { get { return Pt ? "Instalar acesso permanente" : "Install permanent access"; } }
        public static string InstallRequested { get { return Pt ? "Pedido enviado. Aguardando a aprovação do técnico..." : "Request sent. Waiting for the technician's approval..."; } }
        public static string InstallConfirm { get { return Pt ? "O técnico aprovou a instalação do acesso permanente (agente do Tactical RMM) neste computador.\n\nInstalar agora? O Windows pedirá permissão de administrador." : "The technician approved installing permanent access (Tactical RMM agent) on this computer.\n\nInstall now? Windows will ask for administrator permission."; } }
        public static string Installing { get { return Pt ? "Instalando o acesso permanente..." : "Installing permanent access..."; } }
        public static string InstallDone { get { return Pt ? "Acesso permanente instalado." : "Permanent access installed."; } }
        public static string InstallFailed { get { return Pt ? "Não foi possível instalar o acesso permanente: {0}" : "Unable to install permanent access: {0}"; } }
        public static string InstallDenied { get { return Pt ? "O técnico não aprovou a instalação." : "The technician did not approve the installation."; } }
        public static string InstallCancelled { get { return Pt ? "Instalação cancelada." : "Installation cancelled."; } }
        public static string NoConfig { get { return Pt ? "Este executável não possui as configurações do servidor. Baixe-o novamente pelo painel do Tactical RMM." : "This executable has no server settings. Download it again from the Tactical RMM dashboard."; } }
    }

    // ---------------------------------------------------------------------
    // Server settings appended to the executable
    // ---------------------------------------------------------------------
    internal sealed class ClientConfig
    {
        private const string Begin = "--TRMMQS-CONFIG-BEGIN--";
        private const string End = "--TRMMQS-CONFIG-END--";

        public string Api;
        public string Token;
        public string Company;
        public bool Insecure;

        public static bool TryLoad(string[] args, out ClientConfig config, out string error)
        {
            config = new ClientConfig();
            error = null;

            try
            {
                string appended = ReadAppendedConfig(Application.ExecutablePath);
                if (appended != null)
                {
                    var json = Encoding.UTF8.GetString(Convert.FromBase64String(appended.Trim()));
                    var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                    config.Api = GetString(data, "api");
                    config.Token = GetString(data, "token");
                    config.Company = GetString(data, "company");
                    object insecure;
                    config.Insecure = data.TryGetValue("insecure", out insecure) && insecure is bool && (bool)insecure;
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine(ex);
            }

#if QS_DEV
            // command line overrides, development builds only (build.ps1 -Dev).
            // Never in release builds: they would let anyone point this program
            // at another server, which then chooses the executable it runs.
            foreach (var arg in args)
            {
                if (arg.StartsWith("--api=")) config.Api = arg.Substring(6);
                else if (arg.StartsWith("--token=")) config.Token = arg.Substring(8);
                else if (arg == "--insecure") config.Insecure = true;
            }
#endif

            if (config.Api != null && !config.Insecure && !config.Api.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                error = Strings.NoConfig;
                return false;
            }

            if (string.IsNullOrEmpty(config.Api) || string.IsNullOrEmpty(config.Token))
            {
                error = Strings.NoConfig;
                return false;
            }

            config.Api = config.Api.TrimEnd('/');
            return true;
        }

        private static string GetString(Dictionary<string, object> data, string key)
        {
            object value;
            return data.TryGetValue(key, out value) && value != null ? value.ToString() : null;
        }

        // the config block is at the end of the file, only read the tail
        private static string ReadAppendedConfig(string path)
        {
            using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                int size = (int)Math.Min(fs.Length, 64 * 1024);
                var buffer = new byte[size];
                fs.Seek(-size, SeekOrigin.End);
                int read = 0;
                while (read < size)
                {
                    int n = fs.Read(buffer, read, size - read);
                    if (n <= 0) break;
                    read += n;
                }

                string tail = Encoding.ASCII.GetString(buffer, 0, read);
                int b = tail.LastIndexOf(Begin, StringComparison.Ordinal);
                if (b < 0) return null;
                int e = tail.IndexOf(End, b, StringComparison.Ordinal);
                if (e < 0) return null;
                b += Begin.Length;
                return tail.Substring(b, e - b);
            }
        }
    }

    // ---------------------------------------------------------------------
    // Tactical RMM api
    // ---------------------------------------------------------------------
    internal sealed class ApiClient
    {
        private readonly ClientConfig config;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        public string SessionId;
        public string SessionSecret;

        public ApiClient(ClientConfig config)
        {
            this.config = config;
            try
            {
                // tls 1.2 (+ 1.3 when available)
                ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072 | (SecurityProtocolType)12288;
            }
            catch (NotSupportedException)
            {
                ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;
            }

            if (config.Insecure)
            {
                ServicePointManager.ServerCertificateValidationCallback = delegate { return true; };
            }
        }

        private HttpWebRequest Create(string method, string path, int timeout)
        {
            var req = (HttpWebRequest)WebRequest.Create(config.Api + path);
            req.Method = method;
            req.Timeout = timeout;
            req.ReadWriteTimeout = timeout;
            req.UserAgent = "TRMM-QuickSupport/" + Program.Version;
            req.Headers["X-QuickSupport-Token"] = config.Token;
            if (SessionSecret != null) req.Headers["X-QuickSupport-Session"] = SessionSecret;
            return req;
        }

        public Dictionary<string, object> Post(string path, Dictionary<string, object> body)
        {
            var req = Create("POST", path, 60000);
            req.ContentType = "application/json";
            var bytes = Encoding.UTF8.GetBytes(json.Serialize(body ?? new Dictionary<string, object>()));
            req.ContentLength = bytes.Length;
            using (var s = req.GetRequestStream()) s.Write(bytes, 0, bytes.Length);

            try
            {
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    var text = reader.ReadToEnd();
                    var obj = json.DeserializeObject(text) as Dictionary<string, object>;
                    return obj ?? new Dictionary<string, object>();
                }
            }
            catch (WebException ex)
            {
                throw new Exception(ReadError(ex), ex);
            }
        }

        public string GetText(string path)
        {
            var req = Create("GET", path, 120000);
            try
            {
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    return reader.ReadToEnd();
                }
            }
            catch (WebException ex)
            {
                throw new Exception(ReadError(ex), ex);
            }
        }

        public void Download(string path, string destination)
        {
            var req = Create("GET", path, 300000);
            try
            {
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var input = resp.GetResponseStream())
                using (var output = File.Create(destination))
                {
                    input.CopyTo(output);
                }
            }
            catch (WebException ex)
            {
                throw new Exception(ReadError(ex), ex);
            }
        }

        private string ReadError(WebException ex)
        {
            try
            {
                var resp = ex.Response as HttpWebResponse;
                if (resp != null)
                {
                    using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    {
                        var obj = json.DeserializeObject(reader.ReadToEnd()) as Dictionary<string, object>;
                        object err;
                        if (obj != null && obj.TryGetValue("error", out err)) return err.ToString();
                    }
                    return "HTTP " + (int)resp.StatusCode;
                }
            }
            catch (Exception)
            {
            }
            return ex.Message;
        }
    }

    // ---------------------------------------------------------------------
    // MeshCentral agent in temporary mode
    // ---------------------------------------------------------------------
    internal sealed class MeshAgentRunner : IDisposable
    {
        private const string AgentName = "TRMMQuickSupportAgent";
        private readonly string workDir;
        private Process process;
        private volatile bool stopping;
        private int restarts;

        public MeshAgentRunner(bool elevated)
        {
            workDir = CreateProtectedDir(elevated);
        }

        // folder only writable by the current user (or, when elevated, only by
        // administrators and SYSTEM)
        public static string CreateProtectedDir(bool elevated)
        {
            string workDir = Path.Combine(Path.GetTempPath(), "TRMMQuickSupport", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.GetDirectoryName(workDir));

            // Only the current user (or, when elevated, only administrators and
            // SYSTEM) can write here. Otherwise a non elevated process could swap
            // the agent executable or plant a DLL next to it before it is started
            // as administrator.
            var security = new DirectorySecurity();
            security.SetAccessRuleProtection(true, false);
            var inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null), FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            security.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null), FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            if (!elevated)
            {
                security.AddAccessRule(new FileSystemAccessRule(WindowsIdentity.GetCurrent().User, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            }
            if (Directory.Exists(workDir)) throw new IOException("work folder already exists");
            Directory.CreateDirectory(workDir, security);
            return workDir;
        }

        public static string Sha256(string path)
        {
            using (var sha = SHA256.Create())
            using (var fs = File.OpenRead(path))
            {
                return BitConverter.ToString(sha.ComputeHash(fs)).Replace("-", "").ToLowerInvariant();
            }
        }

        public string ExePath { get { return Path.Combine(workDir, AgentName + ".exe"); } }
        // the agent reads <exe name>.msh and <exe name>.tag from its own folder
        private string MshPath { get { return Path.Combine(workDir, AgentName + ".msh"); } }
        private string TagPath { get { return Path.Combine(workDir, AgentName + ".tag"); } }

        public static string Arch
        {
            get
            {
                string arch = (Environment.GetEnvironmentVariable("PROCESSOR_ARCHITEW6432")
                    ?? Environment.GetEnvironmentVariable("PROCESSOR_ARCHITECTURE") ?? "").ToUpperInvariant();
                if (arch == "ARM64") return "arm64";
                return Environment.Is64BitOperatingSystem ? "amd64" : "386";
            }
        }

        public void WriteSettings(string msh, string tag)
        {
            File.WriteAllText(MshPath, msh, new UTF8Encoding(false));
            // the agent tag is only read from the .tag file (a Tag= line in the .msh is discarded)
            if (!string.IsNullOrEmpty(tag)) File.WriteAllText(TagPath, tag, new UTF8Encoding(false));
        }

        public void Start()
        {
            stopping = false;
            // "connect" = temporary agent (MeshAgent meshservice/ServiceMain.c), it must
            // be exactly argv[1]; "-connect" is only understood by the linux installer
            var psi = new ProcessStartInfo(ExePath, "connect")
            {
                WorkingDirectory = workDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            process = Process.Start(psi);
            process.EnableRaisingEvents = true;
            process.Exited += OnExited;
        }

        // restart the agent if it dies while the window is open
        private void OnExited(object sender, EventArgs e)
        {
            if (stopping || restarts >= 5) return;
            restarts++;
            Thread.Sleep(2000);
            if (stopping) return;
            try { Start(); } catch (Exception ex) { Debug.WriteLine(ex); }
        }

        public void Stop()
        {
            stopping = true;
            try
            {
                if (process != null && !process.HasExited)
                {
                    // kill the agent and its child processes (desktop/terminal sessions)
                    var kill = new ProcessStartInfo("taskkill.exe", "/PID " + process.Id + " /T /F")
                    {
                        UseShellExecute = false,
                        CreateNoWindow = true,
                    };
                    using (var p = Process.Start(kill)) p.WaitForExit(10000);
                    if (!process.HasExited) process.Kill();
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine(ex);
            }
        }

        public void Dispose()
        {
            Stop();
            // files can stay locked for a moment after the process ends
            for (int i = 0; i < 10; i++)
            {
                try
                {
                    if (Directory.Exists(workDir)) Directory.Delete(workDir, true);
                    break;
                }
                catch (Exception)
                {
                    Thread.Sleep(500);
                }
            }
        }
    }

    // ---------------------------------------------------------------------
    // Main window
    // ---------------------------------------------------------------------
    internal sealed class MainForm : Form
    {
        private readonly ClientConfig config;
        private readonly ApiClient api;
        private MeshAgentRunner agent;
        private readonly System.Windows.Forms.Timer heartbeatTimer = new System.Windows.Forms.Timer();
        private string code = "";
        private bool sessionOpen;
        private bool closingConfirmed;
        private int heartbeatFailures;

        private readonly Label companyLabel = new Label();
        private readonly Label hintLabel = new Label();
        private readonly Label codeLabel = new Label();
        private readonly Label statusLabel = new Label();
        private readonly Label infoLabel = new Label();
        private readonly Label messageLabel = new Label();
        // shown while the session is created and the agent is downloaded/prepared
        private readonly ProgressBar prepareBar = new ProgressBar();
        private readonly Button copyButton = new Button();
        private readonly Button adminButton = new Button();
        private readonly Button endButton = new Button();
        private readonly Button installButton = new Button();
        private string installState = "";
        private bool installRunning;
        private int normalInterval = 30000;
        // false until the server confirms the mesh agent is connected to
        // meshcentral (before that the technician can't connect yet)
        private bool agentReady;
        private DateTime preparingSince;
        private string formattedCode = "";
        private string welcomeMessage = "";
        // the status (preparing / ready / technician connected) is checked
        // every few seconds so the window follows the technician quickly
        // (the server allows 120 heartbeats/min per ip)
        private const int PreparingInterval = 3000;
        private const int ActiveInterval = 3000;

        private static readonly Color Accent = Color.FromArgb(25, 118, 210);

        public MainForm(ClientConfig config)
        {
            this.config = config;
            api = new ApiClient(config);
            BuildUi();
        }

        private void BuildUi()
        {
            Text = Strings.Title + (string.IsNullOrEmpty(config.Company) ? "" : " - " + config.Company);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            AutoScaleMode = AutoScaleMode.Dpi;
            ClientSize = new Size(440, 400);
            BackColor = Color.White;
            Font = new Font("Segoe UI", 9.75f);
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }

            var header = new Panel { Dock = DockStyle.Top, Height = 56, BackColor = Accent };
            companyLabel.Text = string.IsNullOrEmpty(config.Company) ? Strings.Title : config.Company;
            companyLabel.ForeColor = Color.White;
            companyLabel.Font = new Font("Segoe UI Semibold", 14f);
            companyLabel.Dock = DockStyle.Fill;
            companyLabel.TextAlign = ContentAlignment.MiddleCenter;
            header.Controls.Add(companyLabel);

            hintLabel.Text = Strings.YourCode;
            hintLabel.SetBounds(20, 70, 400, 22);
            hintLabel.TextAlign = ContentAlignment.MiddleCenter;

            codeLabel.Text = "--- --- ---";
            codeLabel.Font = new Font("Consolas", 30f, FontStyle.Bold);
            codeLabel.ForeColor = Accent;
            codeLabel.SetBounds(20, 94, 400, 56);
            codeLabel.TextAlign = ContentAlignment.MiddleCenter;

            copyButton.Text = Strings.CopyCode;
            copyButton.SetBounds(150, 154, 140, 30);
            copyButton.Enabled = false;
            copyButton.Click += delegate
            {
                try
                {
                    Clipboard.SetText(code);
                    statusLabel.Text = Strings.Copied;
                }
                catch (Exception) { }
            };

            statusLabel.Text = Strings.Connecting;
            statusLabel.Font = new Font("Segoe UI Semibold", 10f);
            statusLabel.SetBounds(20, 192, 400, 24);
            statusLabel.TextAlign = ContentAlignment.MiddleCenter;

            messageLabel.SetBounds(20, 218, 400, 40);
            messageLabel.TextAlign = ContentAlignment.MiddleCenter;
            messageLabel.ForeColor = Color.DimGray;

            prepareBar.Style = ProgressBarStyle.Marquee;
            prepareBar.MarqueeAnimationSpeed = 30;
            prepareBar.SetBounds(120, 228, 200, 12);

            infoLabel.Text = Strings.Info;
            infoLabel.SetBounds(20, 258, 400, 50);
            infoLabel.TextAlign = ContentAlignment.MiddleCenter;
            infoLabel.ForeColor = Color.Gray;
            infoLabel.Font = new Font("Segoe UI", 8.25f);

            bool isAdmin = IsAdministrator();
            adminButton.Text = isAdmin ? Strings.RunningAsAdmin : Strings.RunAsAdmin;
            adminButton.Enabled = !isAdmin;
            adminButton.SetBounds(20, 314, 210, 32);
            adminButton.Click += delegate { RestartAsAdmin(); };

            endButton.Text = Strings.EndSupport;
            endButton.SetBounds(250, 314, 170, 32);
            endButton.Click += delegate { Close(); };

            installButton.Text = Strings.InstallPermanent;
            installButton.SetBounds(20, 354, 400, 32);
            installButton.Enabled = false;
            installButton.Click += delegate { RequestInstall(); };

            Controls.AddRange(new Control[] { header, hintLabel, codeLabel, copyButton, statusLabel, prepareBar, messageLabel, infoLabel, adminButton, endButton, installButton });

            heartbeatTimer.Tick += delegate { Heartbeat(); };
            Shown += delegate { StartSession(); };
        }

        private static bool IsAdministrator()
        {
            try
            {
                return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void RestartAsAdmin()
        {
            var psi = new ProcessStartInfo(Application.ExecutablePath)
            {
                UseShellExecute = true,
                Verb = "runas",
                // the new instance waits for this one to exit (single instance mutex)
                Arguments = "--wait=" + Process.GetCurrentProcess().Id,
            };
            try
            {
                Process.Start(psi);
            }
            catch (Exception)
            {
                // user cancelled the UAC prompt, keep the current session
                return;
            }
            closingConfirmed = true;
            Close();
        }

        // Color.Empty = default text color
        private void SetStatus(string text, Color color)
        {
            statusLabel.Text = text;
            statusLabel.ForeColor = color.IsEmpty ? ForeColor : color;
        }

        private void StartSession()
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    var body = new Dictionary<string, object>
                    {
                        { "hostname", Environment.MachineName },
                        { "username", Environment.UserDomainName + "\\" + Environment.UserName },
                        { "os", Environment.OSVersion.VersionString + (Environment.Is64BitOperatingSystem ? " 64-bit" : " 32-bit") },
                        { "version", Program.Version },
                        { "arch", MeshAgentRunner.Arch },
                    };
                    var resp = api.Post("/quicksupport/client/sessions/", body);
                    api.SessionId = resp["session_id"].ToString();
                    api.SessionSecret = resp["session_secret"].ToString();
                    sessionOpen = true;
                    code = resp["code"].ToString();
                    string formatted = resp["formatted_code"].ToString();
                    string message = resp.ContainsKey("welcome_message") && resp["welcome_message"] != null ? resp["welcome_message"].ToString() : "";
                    int interval = 30;
                    if (resp.ContainsKey("heartbeat_interval")) int.TryParse(resp["heartbeat_interval"].ToString(), out interval);

                    Ui(delegate { SetStatus(Strings.Downloading, Color.DarkOrange); });

                    agent = new MeshAgentRunner(IsAdministrator());
                    string tag = resp.ContainsKey("tag") && resp["tag"] != null ? resp["tag"].ToString() : null;
                    agent.WriteSettings(resp["msh"].ToString(), tag);
                    api.Download("/quicksupport/client/meshagent/?arch=" + MeshAgentRunner.Arch, agent.ExePath);

                    // only run the agent if it is exactly the one the server announced
                    string expected = resp.ContainsKey("agent_sha256") && resp["agent_sha256"] != null ? resp["agent_sha256"].ToString().ToLowerInvariant() : "";
                    if (expected.Length != 64 || MeshAgentRunner.Sha256(agent.ExePath) != expected)
                    {
                        agent.Dispose(); // delete the rejected file right away
                        agent = null;
                        throw new Exception(Strings.BadAgent);
                    }
                    agent.Start();

                    // keep "please wait" until the agent is connected to the server
                    Ui(delegate
                    {
                        formattedCode = formatted;
                        welcomeMessage = message;
                        normalInterval = Math.Min(Math.Max(interval, 3) * 1000, ActiveInterval);
                        preparingSince = DateTime.UtcNow;
                        heartbeatTimer.Interval = PreparingInterval;
                        heartbeatTimer.Start();
                        Heartbeat();
                    });
                }
                catch (Exception ex)
                {
                    Ui(delegate
                    {
                        prepareBar.Visible = false;
                        SetStatus("", Color.Empty);
                        MessageBox.Show(this, string.Format(Strings.Failed, ex.Message), Strings.Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
                        closingConfirmed = true;
                        Close();
                    });
                }
            });
        }

        private bool heartbeatRunning;

        private void ShowReady()
        {
            agentReady = true;
            codeLabel.Text = formattedCode;
            copyButton.Enabled = true;
            prepareBar.Visible = false;
            messageLabel.Text = welcomeMessage;
            SetStatus(Strings.Waiting, Color.ForestGreen);
            if (heartbeatTimer.Interval == PreparingInterval) heartbeatTimer.Interval = normalInterval;
            installButton.Enabled = !config.Insecure;
        }

        private void Heartbeat()
        {
            if (heartbeatRunning || !sessionOpen) return;
            heartbeatRunning = true;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    bool preparing = !agentReady;
                    Dictionary<string, object> body = null;
                    if (preparing) body = new Dictionary<string, object> { { "check_agent", true } };
                    var resp = api.Post("/quicksupport/client/sessions/" + api.SessionId + "/heartbeat/", body);
                    heartbeatFailures = 0;
                    string status = resp.ContainsKey("status") ? resp["status"].ToString() : "";
                    string tech = resp.ContainsKey("technician") && resp["technician"] != null ? resp["technician"].ToString() : "";
                    string install = resp.ContainsKey("install_status") && resp["install_status"] != null ? resp["install_status"].ToString() : "";
                    // older servers don't send agent_online: ready right away
                    bool online = !resp.ContainsKey("agent_online") || resp["agent_online"] is bool && (bool)resp["agent_online"];
                    Ui(delegate
                    {
                        if (preparing && !agentReady)
                        {
                            if (status == "closed") { SessionEndedByServer(); return; }
                            if (!online && status != "connected")
                            {
                                bool slow = (DateTime.UtcNow - preparingSince).TotalSeconds > 120;
                                SetStatus(slow ? Strings.StillPreparing : Strings.Downloading, Color.DarkOrange);
                                return;
                            }
                            ShowReady();
                        }
                        OnInstallStatus(install);
                        if (status == "connected")
                        {
                            SetStatus(string.IsNullOrEmpty(tech) ? Strings.Connected : string.Format(Strings.ConnectedBy, tech), Accent);
                        }
                        else if (status == "waiting")
                        {
                            SetStatus(Strings.Waiting, Color.ForestGreen);
                        }
                        else if (status == "closed") SessionEndedByServer();
                    });
                }
                catch (Exception)
                {
                    heartbeatFailures++;
                    Ui(delegate { SetStatus(Strings.Retrying, Color.DarkOrange); });
                }
                finally
                {
                    heartbeatRunning = false;
                }
            });
        }

        // ---------------------------------------------------------- permanent access
        private void RequestInstall()
        {
            installButton.Enabled = false;
            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    api.Post("/quicksupport/client/sessions/" + api.SessionId + "/install/request/", null);
                    Ui(delegate
                    {
                        installState = "requested";
                        messageLabel.Text = Strings.InstallRequested;
                        // check for the approval more often while waiting
                        heartbeatTimer.Interval = 5000;
                    });
                }
                catch (Exception ex)
                {
                    Ui(delegate
                    {
                        installButton.Enabled = true;
                        messageLabel.Text = string.Format(Strings.InstallFailed, ex.Message);
                    });
                }
            });
        }

        private void OnInstallStatus(string status)
        {
            if (status == installState) return;
            installState = status;
            if (status == "requested")
            {
                installButton.Enabled = false;
                messageLabel.Text = Strings.InstallRequested;
                heartbeatTimer.Interval = 5000;
            }
            else if (status == "approved" && !installRunning)
            {
                heartbeatTimer.Interval = normalInterval;
                RunInstaller();
            }
            else if (status == "denied")
            {
                heartbeatTimer.Interval = normalInterval;
                installButton.Enabled = true;
                messageLabel.Text = Strings.InstallDenied;
            }
            else if (status == "done")
            {
                installButton.Enabled = false;
                messageLabel.Text = Strings.InstallDone;
            }
        }

        private void RunInstaller()
        {
            var answer = MessageBox.Show(this, Strings.InstallConfirm, Strings.Title, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes)
            {
                installButton.Enabled = true;
                messageLabel.Text = Strings.InstallCancelled;
                return;
            }
            installRunning = true;
            messageLabel.Text = Strings.Installing;
            ThreadPool.QueueUserWorkItem(delegate
            {
                bool ok = false;
                string result = "";
                string dir = null;
                try
                {
                    string script = api.GetText("/quicksupport/client/sessions/" + api.SessionId + "/installer/?arch=" + MeshAgentRunner.Arch);
                    dir = MeshAgentRunner.CreateProtectedDir(IsAdministrator());
                    string file = Path.Combine(dir, "trmm-install.ps1");
                    // utf-8 with BOM: windows powershell 5 reads it correctly
                    File.WriteAllText(file, script, new UTF8Encoding(true));
                    // the elevated process re-checks the script hash before running it
                    // from memory: the file can't be swapped during the UAC prompt
                    string hash;
                    using (var sha = SHA256.Create())
                    {
                        hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(script))).Replace("-", "").ToLowerInvariant();
                    }
                    string elevated =
                        "$t = [IO.File]::ReadAllText('" + file.Replace("'", "''") + "', [Text.Encoding]::UTF8); " +
                        "$h = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash(" +
                        "[Text.Encoding]::UTF8.GetBytes($t))).Replace('-', '').ToLower(); " +
                        "if ($h -ne '" + hash + "') { exit 97 }; & ([ScriptBlock]::Create($t)); exit $LASTEXITCODE";
                    string encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(elevated));
                    var psi = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -EncodedCommand " + encoded)
                    {
                        UseShellExecute = true,
                        Verb = "runas",
                        WindowStyle = ProcessWindowStyle.Normal,
                    };
                    using (var p = Process.Start(psi))
                    {
                        p.WaitForExit();
                        ok = p.ExitCode == 0;
                        result = "exit code " + p.ExitCode;
                    }
                }
                catch (System.ComponentModel.Win32Exception)
                {
                    result = Strings.InstallCancelled; // UAC denied
                }
                catch (Exception ex)
                {
                    result = ex.Message;
                }
                finally
                {
                    if (dir != null) { try { Directory.Delete(dir, true); } catch (Exception) { } }
                }
                try
                {
                    api.Post("/quicksupport/client/sessions/" + api.SessionId + "/install/result/",
                        new Dictionary<string, object> { { "ok", ok }, { "message", result } });
                }
                catch (Exception ex) { Debug.WriteLine(ex); }
                Ui(delegate
                {
                    installRunning = false;
                    installState = ok ? "done" : "failed";
                    installButton.Enabled = !ok;
                    messageLabel.Text = ok ? Strings.InstallDone : string.Format(Strings.InstallFailed, result);
                });
            });
        }

        private void SessionEndedByServer()
        {
            heartbeatTimer.Stop();
            sessionOpen = false;
            if (agent != null) agent.Stop();
            codeLabel.Text = "--- --- ---";
            copyButton.Enabled = false;
            installButton.Enabled = false;
            SetStatus(Strings.Ended, Color.Empty);
        }

        private void Ui(MethodInvoker action)
        {
            if (IsDisposed) return;
            try
            {
                if (InvokeRequired) BeginInvoke(action);
                else action();
            }
            catch (ObjectDisposedException) { }
            catch (InvalidOperationException) { }
        }

        private void EndSession()
        {
            heartbeatTimer.Stop();
            if (agent != null)
            {
                agent.Dispose();
                agent = null;
            }
            if (sessionOpen)
            {
                sessionOpen = false;
                try { api.Post("/quicksupport/client/sessions/" + api.SessionId + "/close/", null); }
                catch (Exception ex) { Debug.WriteLine(ex); }
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!closingConfirmed && sessionOpen && e.CloseReason == CloseReason.UserClosing)
            {
                var r = MessageBox.Show(this, Strings.ConfirmClose, Strings.Title, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (r != DialogResult.Yes)
                {
                    e.Cancel = true;
                    return;
                }
            }
            Cursor = Cursors.WaitCursor;
            EndSession();
            base.OnFormClosing(e);
        }
    }
}
