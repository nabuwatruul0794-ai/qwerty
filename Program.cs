using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;
using UglyToad.PdfPig;
using UglyToad.PdfPig.DocumentLayoutAnalysis.TextExtractor;

// ---------- Вспомогательные ----------
static string FindEnv()
{
    var dir = new DirectoryInfo(AppContext.BaseDirectory);
    while (dir != null)
    {
        var path = Path.Combine(dir.FullName, ".env");
        if (File.Exists(path)) return path;
        dir = dir.Parent;
    }
    throw new FileNotFoundException("Файл .env не найден");
}

static string? FindBooksDir()
{
    var dir = new DirectoryInfo(AppContext.BaseDirectory);
    while (dir != null)
    {
        var path = Path.Combine(dir.FullName, "books");
        if (Directory.Exists(path)) return path;
        dir = dir.Parent;
    }
    return null;
}

// ---------- Чтение .env ----------
var envDict = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
try
{
    foreach (var line in File.ReadAllLines(FindEnv()))
    {
        var trimmed = line.Trim();
        if (string.IsNullOrEmpty(trimmed) || trimmed.StartsWith("#")) continue;
        var i = trimmed.IndexOf('=');
        if (i > 0)
        {
            var key = trimmed[..i].Trim();
            var value = trimmed[(i + 1)..].Trim().Trim('"').Trim('\'');
            envDict[key] = value;
        }
    }
}
catch (Exception ex)
{
    Console.WriteLine($"Предупреждение: {ex.Message}. Будем использовать переменные окружения.");
}

// Функция получения значения
string GetEnv(string key, string? def = null)
{
    if (envDict.TryGetValue(key, out var v) && !string.IsNullOrEmpty(v)) return v;
    var ev = Environment.GetEnvironmentVariable(key);
    if (!string.IsNullOrEmpty(ev)) return ev;
    if (def != null) return def;
    throw new Exception($"Отсутствует переменная {key}");
}

// ---------- Конфиг ----------
string token = GetEnv("TELEGRAM_BOT_TOKEN");
long ownerId = long.Parse(GetEnv("OWNER_ID"));
string zenKey = GetEnv("OPENCODE_API_KEY");
string model = GetEnv("OPENCODE_MODEL", "claude-sonnet-5-5");
string publicUrl = GetEnv("PUBLIC_URL").TrimEnd('/');

// Белый список
var allowedUsers = new HashSet<long>
{
    ownerId,
    1329641372,
    1079330932,
    1079918562,
};

// Http клиенты
var http = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };
var llm = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
string apiBase = $"https://api.telegram.org/bot{token}/";

// Модели
var models = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
{
    ["haiku"] = "claude-haiku-5-5",
    ["sonnet"] = "claude-sonnet-5-5",
    ["opus"] = "claude-opus-5-5",
    ["qwen"] = "qwen3.8-flash",
    ["deepseek"] = "deepseek-v4-pro",
    ["deepseek-flash"] = "deepseek-v4.1-flash",
    ["kimi"] = "kimi-k3",
    ["glm"] = "glm-5.3-flash",
    ["gpt-mini"] = "gpt-5.4-mini",
    ["gpt-nano"] = "gpt-5.4-nano",
    ["luna"] = "gpt-6-luna",
};

// ---------- Промпт ----------
const string SystemPrompt = """
Ты помощник учителя физики, математики и информатики в Беларуси. Ты составляешь подробный план-конспект урока.

Пиши по-русски простым текстом, без символов Markdown (без **, #, таблиц с |).
Заголовки разделов пиши с новой строки, списки через тире (строка начинается с «- »).

Структура плана-конспекта:
1. Шапка: Учебный предмет, Класс, Глава, Тема урока, Учебное пособие (с указанием страниц),
   Тип урока, Продолжительность (45 минут).
2. Цель урока.
3. Задачи урока: образовательные, развивающие, воспитательные.
4. Планируемые результаты (к концу урока учащиеся смогут...).
5. Методы и формы работы.
6. Оборудование: демонстрационное, раздаточное, дополнительное.
7. Ход урока с указанием минут (в сумме 45):
   1) Организационный момент (2 мин)
   2) Актуализация знаний (5 мин). Вопросы для фронтальной беседы OBЯЗАТЕЛЬНО должны содержать краткие правильные ответы в скобках после каждого вопроса (для самопроверки учителя).
   3) Мотивация и постановка проблемы (3 мин): демонстрация или проблемный вопрос. ТЫ ОБЯЗАН привести не просто ответ, а подробное физическое/логическое объяснение этой проблемы, чтобы учитель мог озвучить его классу.
   4) Изложение нового материала (около 22 мин) по подпунктам 4.1, 4.2 и так далее с минутами,
      с записью в тетрадь, опытами, формулами и определениями.
   5) Закрепление (8 мин): утверждения «верно или неверно» с ответами, контрольные вопросы с ответами. Если приводится практическая задача, ТЫ ОБЯЗАН расписать ее ПОЛНОЕ И ПОДРОБНОЕ РЕШЕНИЕ: краткое условие (Дано / Найти), перевод величин в СИ, базовые формулы, пошаговые математические вычисления и финальный численный ответ.
   6) Рефлексия (2 мин)
   7) Информация о домашнем задании (3 мин): номера параграфов, вопросов или задач. Если учителю предлагается задать на дом задачи, ТЫ ОБЯЗАН прямо в конспекте расписать их полное пошаговое решение и ответы, чтобы учителю не пришлось считать самостоятельно при проверке.
8. Главные выводы (запись в тетрадь).
9. Литература.

Правила:
- К абсолютно любой sгенерированной задаче, проблемному вопросу или практическому заданию на любом этапе урока ТЫ ОБЯЗАН предоставить ПОЛНОЕ ХОД РЕШЕНИЯ с формулами, промежуточными расчетами и финальным ответом. Не ограничивайся только кратким ответом.
- Если в запросе приведён текст параграфа учебника, строй план строго на нём: используй его определения, примеры, опыты, контрольные вопросы и упражнения.
- Не выдумывай номера страниц, рисунков, контрольных вопросов и упражнений учебника. Если нужных данных нет, pиши «[уточнить по учебнику]».
- Актуализация знаний только по уже изученному материалу, не по темам, которые идут позже.
- Если ответ на задачу зависит от рисунка, добавь пометку «сверьте с рисунком в вашем экземпляре учебника».
- Физические факты должны быть точными. Если не уверен, пометь это для учителя.
- Не используй данные об учениках.
""";

// ---------- Книги ----------
var books = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);

async Task<Dictionary<string, List<string>>> LoadBooks()
{
    var result = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
    var dir = FindBooksDir();
    if (dir == null)
    {
        Console.WriteLine("Папка books не найдена.");
        return result;
    }
var tasks = Directory.GetFiles(dir, "*.pdf").Select(file => Task.Run(() =>
    {
        try
        {
            var lines = new List<string>();
            using var pdf = PdfDocument.Open(file);
            foreach (var page in pdf.GetPages())
            {
                var pageText = ContentOrderTextExtractor.GetText(page);
                foreach (var l in pageText.Split('\n'))
                {
                    var t = l.Trim();
                    if (t.Length > 0) lines.Add(t);
                }
            }
            var name = Path.GetFileNameWithoutExtension(file);
            Console.WriteLine($"Загружена книга: {Path.GetFileName(file)} ({lines.Count} строк)");
            return (name, lines);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"Не удалось прочитать {Path.GetFileName(file)}: {ex.Message}");
            return (null, (List<string>)null);
        }
    }));
    foreach (var t in await Task.WhenAll(tasks))
    {
        if (t.name != null) result[t.name] = t.lines;
    }
    return result;
}

// ---------- Поиск параграфа ----------
(string? text, string? bookName) FindParagraph(string request)
{
    const RegexOptions I = RegexOptions.IgnoreCase;
    const int MaxChars = 25000;

    var gradeRx = new Regex(@"(\d{1,2})\s*(?:-?\s*[а-яё]{1,2})?\s*(?:класс\p{L}*|кл\b)", I);
    var gm = gradeRx.Match(request);
    string? grade = gm.Success ? gm.Groups[1].Value : null;
    string noGrade = gradeRx.Replace(request, " ");

    string? subject = null;
    bool explicitSubject = true;

    if (Regex.IsMatch(request, @"(?<!\p{L})физик", I)) subject = "fizika";
    else if (Regex.IsMatch(request, @"(?<!\p{L})информатик", I)) subject = "informatika";
    else if (Regex.IsMatch(request, @"(?<!\p{L})алгебр", I)) subject = "algebra";
    else if (Regex.IsMatch(request, @"(?<!\p{L})геометри", I)) subject = "geometriya";
    else if (Regex.IsMatch(request, @"(?<!\p{L})математик", I)) subject = "matem";
    else
    {
        explicitSubject = false;
        if (Regex.IsMatch(request, @"(?<!\p{L})(?:газ|механик|кинематик|термодинамик|электр|оптик|магнит|давлени)", I)) subject = "fizika";
        else if (Regex.IsMatch(request, @"(?<!\p{L})(?:функци|уравнени|неравенств|тригонометр|логарифм|прогресси)", I)) subject = "algebra";
        else if (Regex.IsMatch(request, @"(?<!\p{L})(?:планиметр|стереометр|вектор|треугольник|окружност)", I)) subject = "geometriya";
        else if (Regex.IsMatch(request, @"(?<!\p{L})(?:программ|алгоритм|паскаль|питон|python|компьютер)", I)) subject = "informatika";
    }

    var candidates = books.ToList();
    if (subject != null)
    {
        var f = candidates.Where(b => b.Key.Contains(subject!, StringComparison.OrdinalIgnoreCase)).ToList();
        if (explicitSubject || f.Count > 0) candidates = f;
    }
    if (grade != null)
    {
        var gr = new Regex($@"-{grade}(?!\d)");
        var f = candidates.Where(b => gr.IsMatch(b.Key)).ToList();
        if (explicitSubject || f.Count > 0) candidates = f;
    }
    if (candidates.Count == 0) return (null, null);

    int num = -1;
    var em = Regex.Match(noGrade, @"(?:§|(?<!\p{L})(?:параграф\p{L}*|пункт\p{L}*|пар|п)\.?)\s*(\d+)", I);
    if (em.Success) num = int.Parse(em.Groups[1].Value);
    else
    {
        var im = Regex.Match(noGrade, @"(?<!\d)\d{1,3}(?!\d)");
        if (im.Success) num = int.Parse(im.Value);
    }

    string[] ignoreStems = { "физик", "инфор", "алгеб", "геоме", "матем", "параг", "урок", "конс", "класс", "соста", "сдела", "план", "тема", "теме", "пожал", "нужен", "нужна" };
    var stems = Regex.Matches(noGrade.ToLowerInvariant(), @"\p{L}{4,}")
        .Select(m => m.Value.Length > 5 ? m.Value[..5] : m.Value)
        .Where(s => !ignoreStems.Contains(s))
        .Distinct()
        .ToList();

    if (num == -1 && stems.Count == 0) return (null, null);

    string? best = null;
    string? bestBook = null;
    int bestScore = 0;

    foreach (var book in candidates)
    {
        var lines = book.Value;
        var starts = new List<int>();
        for (int idx = 0; idx < lines.Count; idx++)
            if (Regex.IsMatch(lines[idx], @"^§\s*\d+")) starts.Add(idx);

        for (int k = 0; k < starts.Count; k++)
        {
            int end = k + 1 < starts.Count ? starts[k + 1] : Math.Min(lines.Count, starts[k] + 600);
            bool hit;
            int score = 0;

            if (num != -1)
            {
                var head = Regex.Match(lines[starts[k]], @"^§\s*(\d+)");
                hit = head.Success && int.Parse(head.Groups[1].Value) == num;
            }
            else
            {
                string title = lines[starts[k]];
                if (starts[k] + 1 < end) title += " " + lines[starts[k] + 1];
                title = title.ToLowerInvariant();
                score = stems.Count(s => title.Contains(s));
                int need = stems.Count == 1 ? 1 : Math.Max(2, (int)Math.Ceiling(stems.Count * 0.6));
                hit = score >= need;
            }

            if (!hit) continue;

            var sb = new StringBuilder();
            for (int j = starts[k]; j < end; j++)
            {
                if (sb.Length + lines[j].Length + 1 > MaxChars) break;
                sb.AppendLine(lines[j]);
            }
            var textRes = sb.ToString().TrimEnd();

            bool better = best == null
                || (num != -1 ? textRes.Length > best.Length
                              : score > bestScore || (score == bestScore && textRes.Length > best.Length));
            if (better)
            {
                best = textRes;
                bestBook = book.Key;
                bestScore = score;
            }
        }
    }
    return (best, bestBook);
}

// ---------- Word ----------
bool IsHeading(string line)
{
    if (line.Length == 0 || line.Length > 90) return false;
    if (Regex.IsMatch(line, @"^\d+(\.\d+)*[\.\)]\s")) return true;
    string[] names = { "План-конспект", "Цель урока", "Задачи урока", "Планируемые результаты", "Методы и формы работы", "Оборудование", "Ход урока", "Главные выводы", "Литература" };
    return names.Any(n => line.StartsWith(n));
}

void MakeDocx(string plan, string path)
{
    using var wd = WordprocessingDocument.Create(path, WordprocessingDocumentType.Document);
    var main = wd.AddMainDocumentPart();
    main.Document = new Document(new Body());
    var body = main.Document.Body!;

    foreach (var raw in plan.Replace("\r", "").Split('\n'))
    {
        var line = raw.TrimEnd();
        bool bullet = line.StartsWith("- ");
        if (bullet) line = "• " + line[2..];
        bool heading = IsHeading(line);

        var rp = new RunProperties(new RunFonts { Ascii = "Times New Roman", HighAnsi = "Times New Roman", ComplexScript = "Times New Roman" });
        if (heading) rp.Append(new Bold());
        rp.Append(new FontSize { Val = "24" });

        var pp = new ParagraphProperties(new SpacingBetweenLines { After = "60" });
        if (bullet) pp.Append(new Indentation { Left = "360" });

        body.Append(new Paragraph(pp, new Run(rp, new Text(line) { Space = SpaceProcessingModeValues.Preserve })));
    }
    main.Document.Save();
}

// ---------- Telegram helpers ----------
async Task Send(long chatId, string text)
{
    if (string.IsNullOrWhiteSpace(text)) text = "(пустой ответ)";
    while (text.Length > 0)
    {
        int len = Math.Min(4000, text.Length);
        if (len < text.Length)
        {
            int cut = text.LastIndexOf('\n', len - 1, len);
            if (cut > 1000) len = cut + 1;
        }
        try { await http.PostAsJsonAsync($"{apiBase}sendMessage", new { chat_id = chatId, text = text[..len] }); } catch { }
        text = text[len..];
    }
}

async Task SendDoc(long chatId, string path, string caption)
{
    try
    {
        using var form = new MultipartFormDataContent();
        form.Add(new StringContent(chatId.ToString()), "chat_id");
        form.Add(new StringContent(caption), "caption");
        form.Add(new ByteArrayContent(await File.ReadAllBytesAsync(path)), "document", Path.GetFileName(path));
        await http.PostAsync($"{apiBase}sendDocument", form);
    }
    catch (Exception ex) { Console.WriteLine(ex.Message); }
}

// ---------- Модель ----------
string ApiFamily(string m) =>
    m.StartsWith("claude-") || m.StartsWith("qwen3.8-flash") || m.StartsWith("qwen3.6") || m.StartsWith("qwen3.5") ? "anthropic"
    : m.StartsWith("gpt-") || m.StartsWith("grok-") || m.StartsWith("muse-") ? "responses"
    : m.StartsWith("gemini-") ? "google"
    : "chat";

string ModelsList()
{
    var sb = new StringBuilder("Доступные модели (команда: /model имя):\n");
    foreach (var kv in models) sb.AppendLine($"- {kv.Key} ({kv.Value})");
    sb.AppendLine($"\nСейчас выбрана: {model}");
    return sb.ToString();
}

async Task<string> AskModel(string userRequest)
{
    string family = ApiFamily(model);
    if (family == "google") return "Модели Gemini бот пока не поддерживает.";

    string url;
    object body;
    switch (family)
    {
case "anthropic":
            url = "https://opencode.ai/inference/anthropic/v1/messages";
            body = new { model, max_tokens = 32000, system = SystemPrompt, messages = new[] { new { role = "user", content = userRequest } } };
            break;
        case "responses":
            url = "https://opencode.ai/inference/openai/v1/responses";
            body = new { model, instructions = SystemPrompt, input = userRequest, max_output_tokens = 16000 };
            break;
        default:
            url = "https://opencode.ai/inference/openai/v1/chat/completions";
            body = new { model, max_tokens = 16000, messages = new object[] { new { role = "system", content = SystemPrompt }, new { role = "user", content = userRequest } } };
            break;
    }

    using var req = new HttpRequestMessage(HttpMethod.Post, url);
    req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", zenKey);
    req.Content = JsonContent.Create(body);

    using var resp = await llm.SendAsync(req);
    var respText = await resp.Content.ReadAsStringAsync();
    if (!resp.IsSuccessStatusCode)
    {
        var shortText = respText.Length > 500 ? respText[..500] : respText;
        return $"Ошибка модели {model} ({(int)resp.StatusCode}): {shortText}";
    }

    using var respDoc = JsonDocument.Parse(respText);
    var root = respDoc.RootElement;
    var result = new StringBuilder();

    if (family == "anthropic")
    {
        foreach (var block in root.GetProperty("content").EnumerateArray())
            if (block.TryGetProperty("text", out var blockText)) result.Append(blockText.GetString());
    }
    else if (family == "responses")
    {
        foreach (var item in root.GetProperty("output").EnumerateArray())
            if (item.TryGetProperty("content", out var parts) && parts.ValueKind == JsonValueKind.Array)
                foreach (var part in parts.EnumerateArray())
                    if (part.TryGetProperty("text", out var partText)) result.Append(partText.GetString());
    }
    else
    {
        var message = root.GetProperty("choices")[0].GetProperty("message");
        if (message.TryGetProperty("content", out var content) && content.ValueKind == JsonValueKind.String)
            result.Append(content.GetString());
    }

    string? stop = family switch
    {
        "anthropic" => root.TryGetProperty("stop_reason", out var sr) ? sr.GetString() : null,
        "responses" => root.TryGetProperty("status", out var st) ? st.GetString() : null,
        _ => root.GetProperty("choices")[0].TryGetProperty("finish_reason", out var fr) ? fr.GetString() : null
    };
    if (stop is "max_tokens" or "incomplete" or "length")
    {
        Console.WriteLine($"Ответ обрезан по лимиту токенов ({model}). stop_reason={stop}");
        result.Append("\n\n[Конспект обрезан по лимиту длины. Попробуйте запросить его снова или переключите модель.]");
    }

    return result.ToString();
}

// ---------- Обработка обновления ----------
async Task HandleUpdate(JsonElement update)
{
    try
    {
        if (!update.TryGetProperty("message", out var msg)) return;
        if (!msg.TryGetProperty("text", out var textEl)) return;

        long fromId = msg.GetProperty("from").GetProperty("id").GetInt64();
        if (!allowedUsers.Contains(fromId)) return;

        long chatId = msg.GetProperty("chat").GetProperty("id").GetInt64();
        string text = (textEl.GetString() ?? "").Trim();
        if (string.IsNullOrEmpty(text)) return;

        if (text.StartsWith("/start"))
        {
            await Send(chatId, "Привет. Бот делает план-конспекты по физике, математике и информатике.\nНапиши класс, предмет и номер параграфа, например: 10 класс, физика, § 25.\n/books - список книг\n/models - модели\n/model имя - сменить модель");
            return;
        }
        if (text.StartsWith("/books"))
        {
            var list = books.Count == 0 ? "Книг нет. Положи PDF в папку books." : string.Join("\n", books.Select(b => $"- {b.Key} ({b.Value.Count} строк)"));
            await Send(chatId, list);
            return;
        }
        if (text.StartsWith("/models"))
        {
            await Send(chatId, ModelsList());
            return;
        }
        if (text.StartsWith("/model"))
        {
            var arg = text[6..].Trim();
            if (models.TryGetValue(arg, out var newModel))
            {
                model = newModel;
                await Send(chatId, $"Модель переключена: {model}");
            }
            else await Send(chatId, "Такой модели нет.\n\n" + ModelsList());
            return;
        }

        string userRequest = text;
        var (paragraph, bookName) = FindParagraph(text);
        if (paragraph != null)
        {
            userRequest += "\n\nТекст параграфа из учебника (строй план на нём):\n" + paragraph;
            await Send(chatId, $"Нашёл параграф в книге «{bookName}» ({paragraph.Length} символов).");
        }
        else if (books.Count == 0)
        {
            // нет книг
        }
        else
        {
            await Send(chatId, "Параграф в книгах не нашёл. Составлю план без текста учебника.");
        }

        await Send(chatId, $"Готовлю план урока ({model}), это займёт 1-2 минуты...");
        var answer = await AskModel(userRequest);

        try
        {
            var path = Path.Combine(Path.GetTempPath(), $"plan_{DateTime.Now:yyyyMMdd_HHmmssfff}.docx");
            MakeDocx(answer, path);
            await SendDoc(chatId, path, "План-конспект урока");
            try { File.Delete(path); } catch { }
        }
        catch (Exception docEx)
        {
            Console.WriteLine("DOCX error: " + docEx.Message);
            await Send(chatId, answer);
        }
    }
    catch (Exception ex)
    {
        Console.WriteLine("HandleUpdate error: " + ex.Message);
    }
}

// ---------- Запуск веб-сервера ----------
foreach (var kv in await LoadBooks()) books[kv.Key] = kv.Value;
Console.WriteLine($"Книг загружено: {books.Count}. Модель: {model}");

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls("http://+:8080");

var app = builder.Build();

app.MapGet("/", () => Results.Ok("Bot is running"));
app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

app.MapPost("/webhook", async (JsonElement update) =>
{
    _ = Task.Run(() => HandleUpdate(update));
    return Results.Ok();
});

app.Lifetime.ApplicationStarted.Register(async () =>
{
    try
    {
        var webhookUrl = $"{publicUrl}/webhook";
        Console.WriteLine($"Устанавливаю вебхук: {webhookUrl}");
        var resp = await http.GetAsync($"{apiBase}setWebhook?url={Uri.EscapeDataString(webhookUrl)}");
        var body = await resp.Content.ReadAsStringAsync();
        Console.WriteLine($"setWebhook ответ: {body}");
    }
    catch (Exception ex)
    {
        Console.WriteLine($"Ошибка установки вебхука: {ex.Message}");
    }
});

app.Run();
