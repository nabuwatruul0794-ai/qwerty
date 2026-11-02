using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;
using UglyToad.PdfPig;
using UglyToad.PdfPig.DocumentLayoutAnalysis.TextExtractor;

static string FindEnv()
{
    // Ищем .env в папке программы и выше (нужно для запуска из Visual Studio)
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
    // Ищем папку books рядом с .env (в папке программы или выше)
    var dir = new DirectoryInfo(AppContext.BaseDirectory);
    while (dir != null)
    {
        var path = Path.Combine(dir.FullName, "books");
        if (Directory.Exists(path)) return path;
        dir = dir.Parent;
    }
    return null;
}

// Читаем настройки из файла .env
var env = new Dictionary<string, string>();
foreach (var line in File.ReadAllLines(FindEnv()))
{
    var i = line.IndexOf('=');
    if (i > 0) env[line[..i].Trim()] = line[(i + 1)..].Trim();
}

string token = env["TELEGRAM_BOT_TOKEN"];
long ownerId = long.Parse(env["OWNER_ID"]);
string zenKey = env["OPENCODE_API_KEY"];
string model = env.GetValueOrDefault("OPENCODE_MODEL", "claude-sonnet-5-5");

// Белый список пользователей, которым разрешен доступ к боту
var allowedUsers = new HashSet<long>
{
    ownerId,       // Ваш ID (автоматически разрешен)
    1329641372,     // ID первого коллеги 
    1079330932,     // ID второго коллеги 
    1079918562,
    // Можно добавлять новых коллег через запятую
};

var http = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };   // Telegram
var llm = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };     // модель
string api = $"https://api.telegram.org/bot{token}/";
long offset = 0;

// Короткие имена платных моделей, которые бот умеет вызывать
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

// Книги: имя файла -> все строки текста
var books = new Dictionary<string, List<string>>();

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
   2) Актуализация знаний (5 мин). Вопросы для фронтальной беседы ОБЯЗАТЕЛЬНО должны содержать краткие правильные ответы в скобках после каждого вопроса (для самопроверки учителя).
   3) Мотивация и постановка проблемы (3 мин): демонстрация или проблемный вопрос. ТЫ ОБЯЗАН привести не просто ответ, а подробное физическое/логическое объяснение этой проблемы, чтобы учитель мог озвучить его классу.
   4) Изложение нового материала (около 22 мин) по подпунктам 4.1, 4.2 и так далее с минутами,
      с записью в тетрадь, опытами, формулами и определениями.
   5) Закрепление (8 мин): утверждения «верно или неверно» с ответами, контрольные вопросы с ответами. Если приводится практическая задача, ТЫ ОБЯЗАН расписать ее ПОЛНОЕ И ПОДРОБНОЕ РЕШЕНИЕ: краткое условие (Дано / Найти), перевод величин в СИ, базовые формулы, пошаговые математические вычисления и финальный численный ответ.
   6) Рефлексия (2 мин)
   7) Информация о домашнем задании (3 мин): номера параграфов, вопросов или задач. Если учителю предлагается задать на дом задачи, ТЫ ОБЯЗАН прямо в конспекте расписать их полное пошаговое решение и ответы, чтобы учителю не пришлось считать самостоятельно при проверке.
8. Главные выводы (запись в тетрадь).
9. Литература.

Правила:
- К абсолютно любой сгенерированной задаче, проблемному вопросу или практическому заданию на любом этапе урока ТЫ ОБЯЗАН предоставить ПОЛНОЕ ХОД РЕШЕНИЯ с формулами, промежуточными расчетами и финальным ответом. Не ограничивайся только кратким ответом.
- Если в запросе приведён текст параграфа учебника, строй план строго на нём: используй его
  определения, примеры, опыты, контрольные вопросы и упражнения. Не вводи понятий, которых в тексте
  параграфа нет и которые выходят за рамки класса. Номера рисунков, вопросов и упражнений бери только из текста.
- Не выдумывай номера страниц, рисунков, контрольных вопросов и упражнений учебника.
  Если нужных данных нет, пиши «[уточнить по учебнику]».
- Актуализация знаний только по уже изученному материалу, не по темам, которые идут позже.
- Если ответ на задачу зависит от рисунка, добавь пометку «сверьте с рисунком в вашем экземпляре учебника».
- Физические факты должны быть точными. Если не уверен, пометь это для учителя.
- Не используй данные об учениках.
""";

// ---------- Книги ----------
Dictionary<string, List<string>> LoadBooks()
{
var result = new Dictionary<string, List<string>>();
var dir = FindBooksDir();
if (dir == null)
{
Console.WriteLine("Папка books не найдена, бот будет работать без учебников.");
return result;
}
foreach (var file in Directory.GetFiles(dir, "*.pdf"))
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
result[Path.GetFileNameWithoutExtension(file)] = lines;
Console.WriteLine($"Загружена книга: {Path.GetFileName(file)} ({lines.Count} строк)");
}
catch (Exception ex)
{
Console.WriteLine($"Не удалось прочитать {Path.GetFileName(file)}: {ex.Message}");
}
}
return result;
}

// Ищет в книгах текст параграфа, автоматически определяя предмет и класс из запроса
(string? text, string? bookName) FindParagraph(string request)
{
    const RegexOptions I = RegexOptions.IgnoreCase;
    const int MaxChars = 25000;

    // ---------- Класс ----------
    // "10 класс", "10-й класс", "10 кл", "10кл."
    var gradeRx = new Regex(@"(\d{1,2})\s*(?:-?\s*[а-яё]{1,2})?\s*(?:класс\p{L}*|кл\b)", I);
    var gm = gradeRx.Match(request);
    string? grade = gm.Success ? gm.Groups[1].Value : null;
    string noGrade = gradeRx.Replace(request, " "); // чтобы "10" не приняли за номер параграфа

    // ---------- Предмет ----------
    // 1) явное название предмета (высший приоритет), только с границей слова
    // 2) косвенные признаки, только если предмет не назван
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

    // ---------- Фильтр книг ----------
    var candidates = books.ToList();

    if (subject != null)
    {
        var f = candidates.Where(b => b.Key.Contains(subject, StringComparison.OrdinalIgnoreCase)).ToList();
        // предмет назван явно -> не откатываемся на чужие книги
        if (explicitSubject || f.Count > 0) candidates = f;
    }
    if (grade != null)
    {
        var gr = new Regex($@"-{grade}(?!\d)"); // "-1" не совпадёт с "-10"
        var f = candidates.Where(b => gr.IsMatch(b.Key)).ToList();
        if (explicitSubject || f.Count > 0) candidates = f;
    }
    if (candidates.Count == 0) return (null, null);

    // ---------- Номер параграфа ----------
    int num = -1;
    var em = Regex.Match(noGrade, @"(?:§|(?<!\p{L})(?:параграф\p{L}*|пункт\p{L}*|пар|п)\.?)\s*(\d+)", I);
    if (em.Success)
    {
        num = int.Parse(em.Groups[1].Value);
    }
    else
    {
        var im = Regex.Match(noGrade, @"(?<!\d)\d{1,3}(?!\d)");
        if (im.Success) num = int.Parse(im.Value);
    }

    // ---------- Основы для поиска по названию темы (если номера нет) ----------
    string[] ignoreStems = { "физик", "инфор", "алгеб", "геоме", "матем", "параг", "урок", "конс", "класс",
                             "соста", "сдела", "план", "тема", "теме", "пожал", "нужен", "нужна" };
    var stems = Regex.Matches(noGrade.ToLowerInvariant(), @"\p{L}{4,}")
        .Select(m => m.Value.Length > 5 ? m.Value[..5] : m.Value)
        .Where(s => !ignoreStems.Contains(s))
        .Distinct()
        .ToList();

    if (num == -1 && stems.Count == 0) return (null, null);

    // ---------- Поиск ----------
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
                // заголовок может переноситься на следующую строку
                string title = lines[starts[k]];
                if (starts[k] + 1 < end) title += " " + lines[starts[k] + 1];
                title = title.ToLowerInvariant();
                score = stems.Count(s => title.Contains(s));
                int need = stems.Count == 1 ? 1 : Math.Max(2, (int)Math.Ceiling(stems.Count * 0.6));
                hit = score >= need;
            }

            if (!hit) continue;

            // собираем текст по строкам, не превышая лимит (режем по границе строки)
            var sb = new StringBuilder();
            for (int j = starts[k]; j < end; j++)
            {
                if (sb.Length + lines[j].Length + 1 > MaxChars) break;
                sb.AppendLine(lines[j]);
            }
            var text = sb.ToString().TrimEnd();

            // по номеру: берём самый длинный (отсекаем упоминания в оглавлении);
            // по названию: берём лучший по совпадению, при равенстве самый длинный
            bool better = best == null
                || (num != -1 ? text.Length > best.Length
                              : score > bestScore || (score == bestScore && text.Length > best.Length));
            if (better)
            {
                best = text;
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
    string[] names =
    {
        "План-конспект", "Цель урока", "Задачи урока", "Планируемые результаты",
        "Методы и формы работы", "Оборудование", "Ход урока", "Главные выводы", "Литература"
    };
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

        var rp = new RunProperties(new RunFonts
        {
            Ascii = "Times New Roman",
            HighAnsi = "Times New Roman",
            ComplexScript = "Times New Roman"
        });
        if (heading) rp.Append(new Bold());
        rp.Append(new FontSize { Val = "24" });

        var pp = new ParagraphProperties(new SpacingBetweenLines { After = "60" });
        if (bullet) pp.Append(new Indentation { Left = "360" });

        body.Append(new Paragraph(pp, new Run(rp, new Text(line) { Space = SpaceProcessingModeValues.Preserve })));
    }
    main.Document.Save();
}

// ---------- Telegram ----------

// Отправка длинного текста частями (в Telegram лимит 4096 символов)
async Task Send(long chatId, string text)
{
    if (string.IsNullOrWhiteSpace(text)) text = "(пустой ответ)";
    while (text.Length > 0)
    {
        int len = Math.Min(4000, text.Length);
        if (len < text.Length)
        {
            // режем по концу строки, чтобы не разорвать слово
            int cut = text.LastIndexOf('\n', len - 1, len);
            if (cut > 1000) len = cut + 1;
        }
        await http.PostAsJsonAsync($"{api}sendMessage", new { chat_id = chatId, text = text[..len] });
        text = text[len..];
    }
}

async Task SendDoc(long chatId, string path, string caption)
{
    using var form = new MultipartFormDataContent();
    form.Add(new StringContent(chatId.ToString()), "chat_id");
    form.Add(new StringContent(caption), "caption");
    form.Add(new ByteArrayContent(await File.ReadAllBytesAsync(path)), "document", Path.GetFileName(path));
    await http.PostAsync($"{api}sendDocument", form);
}

// ---------- Модель ----------

// Какой формат запроса нужен модели (так устроен OpenCode Zen)
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

// Запрос к модели через OpenCode Zen
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
            body = new
            {
                model,
                max_tokens = 8000,
                system = SystemPrompt,
                messages = new[] { new { role = "user", content = userRequest } }
            };
            break;
        case "responses":
            url = "https://opencode.ai/inference/openai/v1/responses";
            body = new
            {
                model,
                instructions = SystemPrompt,
                input = userRequest,
                max_output_tokens = 16000
            };
            break;
        default:
            url = "https://opencode.ai/inference/openai/v1/chat/completions";
            body = new
            {
                model,
                max_tokens = 16000,
                messages = new object[]
                {
                    new { role = "system", content = SystemPrompt },
                    new { role = "user", content = userRequest }
                }
            };
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

    if (root.TryGetProperty("usage", out var usage) && usage.TryGetProperty("cost", out var cost))
        Console.WriteLine($"Стоимость запроса ({model}): ${cost}");

    return result.ToString();
}

// ---------- Запуск ----------

foreach (var kv in LoadBooks()) books[kv.Key] = kv.Value;
Console.WriteLine($"Бот запущен. Модель: {model}. Книг загружено: {books.Count}. Чтобы остановить, закрой окно или нажми Ctrl+C.");

while (true)
{
    try
    {
        // Ждём новые сообщения (до 30 секунд)
        var json = await http.GetStringAsync($"{api}getUpdates?timeout=30&offset={offset}");
        using var doc = JsonDocument.Parse(json);

        foreach (var upd in doc.RootElement.GetProperty("result").EnumerateArray())
        {
            offset = upd.GetProperty("update_id").GetInt64() + 1;

            if (!upd.TryGetProperty("message", out var msg)) continue;
            if (!msg.TryGetProperty("text", out var textEl)) continue;

            // Отвечаем только владельцу, остальных игнорируем
            long fromId = msg.GetProperty("from").GetProperty("id").GetInt64();

            // Если пользователя нет в белом списке — игнорируем его
            if (!allowedUsers.Contains(fromId))
            {
                // Опционально: можно отправить один раз сообщение, что доступ закрыт
                // await Send(chatId, "Извините, этот бот приватный и доступен только для ограниченного круга учителей.");
                continue;
            }

            long chatId = msg.GetProperty("chat").GetProperty("id").GetInt64();
            string text = (textEl.GetString() ?? "").Trim();

            // Команды
            if (text.StartsWith("/start"))
            {
                await Send(chatId, "Привет. Этот бот умеет делать план-конспекты урока по математике, физике и информатике. \n" +
                                   "Напиши класс, предмет и номер параграфа, например: 8 класс, физика, § 13.\n" +
                                   "Бот найдёт параграф в учебнике из папки books и пришлёт план-конспект файлом Word.\n" +
                                   "/models: список моделей, /model имя: переключить модель, /books: загруженные книги.");
                continue;
            }
            if (text.StartsWith("/books"))
            {
                var list = books.Count == 0
                    ? "Книг нет. Положи PDF в папку books и перезапусти бота."
                    : string.Join("\n", books.Select(b => $"- {b.Key} ({b.Value.Count} строк)"));
                await Send(chatId, list);
                continue;
            }
            if (text.StartsWith("/models"))
            {
                await Send(chatId, ModelsList());
                continue;
            }
            if (text.StartsWith("/model"))
            {
                var arg = text[6..].Trim();
                if (models.TryGetValue(arg, out var newModel))
                {
                    model = newModel;
                    await Send(chatId, $"Модель переключена: {model}");
                }
                else
                {
                    await Send(chatId, "Такой модели нет в списке.\n\n" + ModelsList());
                }
                continue;
            }

            // Ищем параграф в учебнике
            string userRequest = text;
            var (paragraph, bookName) = FindParagraph(text);
            if (paragraph != null)
            {
                userRequest += "\n\nТекст параграфа из учебника (строй план на нём):\n" + paragraph;
                await Send(chatId, $"Нашёл параграф в книге «{bookName}» ({paragraph.Length} символов).");
            }
            else if (books.Count > 0)
            {
                await Send(chatId, "Параграф в книгах не нашёл (проверь предмет, класс и номер). Составлю план без текста учебника.");
            }

            await Send(chatId, $"Готовлю план урока ({model}), это займёт 1-2 минуты...");
            try
            {
                var answer = await AskModel(text.Length > 0 ? userRequest : text);

                // Пробуем отправить файлом Word, если не вышло, отправляем текстом
                try
                {
                    var path = Path.Combine(Path.GetTempPath(), $"plan_{DateTime.Now:yyyyMMdd_HHmmss}.docx");
                    MakeDocx(answer, path);
                    await SendDoc(chatId, path, "План-конспект урока");
                    File.Delete(path);
                }
                catch (Exception docEx)
                {
                    Console.WriteLine("Не удалось создать Word: " + docEx.Message);
                    await Send(chatId, answer);
                }
            }
            catch (Exception ex)
            {
                await Send(chatId, "Не получилось: " + ex.Message);
            }
        }
    }
    catch (Exception ex)
    {
        Console.WriteLine("Ошибка: " + ex.Message);
        await Task.Delay(5000);
    }
}