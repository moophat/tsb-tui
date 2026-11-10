/*
 * tsb-tui.exe: starts the bundled Python on the TUI, with the environment the
 * bundle needs, and returns Python's exit code. No argument is parsed or
 * re-quoted; the user's command line reaches Python as typed.
 */
#include <windows.h>
#include <conio.h>
#include <stdlib.h>
#include <wchar.h>

#include "cmdline.h"

static void emit(const wchar_t *text)
{
    HANDLE err = GetStdHandle(STD_ERROR_HANDLE);
    DWORD mode, written;
    int bytes;
    char *utf8;

    if (err == INVALID_HANDLE_VALUE || err == NULL)
        return;
    if (GetConsoleMode(err, &mode)) {
        WriteConsoleW(err, text, (DWORD)wcslen(text), &written, NULL);
        return;
    }
    bytes = WideCharToMultiByte(CP_UTF8, 0, text, -1, NULL, 0, NULL, NULL);
    if (bytes <= 1)
        return;
    utf8 = malloc((size_t)bytes);
    if (utf8 == NULL)
        return;
    WideCharToMultiByte(CP_UTF8, 0, text, -1, utf8, bytes, NULL, NULL);
    WriteFile(err, utf8, (DWORD)(bytes - 1), &written, NULL);
    free(utf8);
}

static void emit_error_code(const wchar_t *what, DWORD code)
{
    wchar_t number[16];

    _snwprintf(number, 16, L"%lu", (unsigned long)code);
    emit(L"tsb-tui: ");
    emit(what);
    emit(L" (Windows error ");
    emit(number);
    emit(L")\r\n");
}

static void *must(void *pointer)
{
    if (pointer == NULL) {
        emit(L"tsb-tui: out of memory\r\n");
        ExitProcess(1);
    }
    return pointer;
}

static wchar_t *join(const wchar_t *a, const wchar_t *b, const wchar_t *c)
{
    size_t la = wcslen(a), lb = wcslen(b), lc = wcslen(c);
    wchar_t *out = must(malloc((la + lb + lc + 1) * sizeof(wchar_t)));

    wmemcpy(out, a, la);
    wmemcpy(out + la, b, lb);
    wmemcpy(out + la + lb, c, lc + 1);
    return out;
}

/* A launcher error: print it, and when a double-click opened this console
 * (the launcher is its only process) wait so the window is not gone unread. */
static int launcher_error(const wchar_t *a, const wchar_t *b, const wchar_t *c)
{
    DWORD only;

    emit(L"tsb-tui: ");
    emit(a);
    emit(b);
    emit(c);
    emit(L"\r\n");
    if (GetConsoleProcessList(&only, 1) == 1) {
        emit(L"Press any key to continue . . .");
        _getch();
        emit(L"\r\n");
    }
    return 1;
}

static BOOL WINAPI on_console_event(DWORD event)
{
    /* Ctrl-C and Ctrl-Break reach Python through the shared console; this
     * process stays alive to return its exit code. */
    return event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT;
}

/* The folder this executable is in, without a trailing backslash. */
static wchar_t *own_folder(void)
{
    DWORD size = MAX_PATH, got;
    wchar_t *path = must(malloc(size * sizeof(wchar_t)));
    wchar_t *slash;

    for (;;) {
        got = GetModuleFileNameW(NULL, path, size);
        if (got == 0) {
            emit_error_code(L"cannot find its own path", GetLastError());
            ExitProcess(1);
        }
        if (got < size)
            break;
        size *= 2;
        path = must(realloc(path, size * sizeof(wchar_t)));
    }
    slash = wcsrchr(path, L'\\');
    if (slash == NULL) {
        emit(L"tsb-tui: its own path has no folder\r\n");
        ExitProcess(1);
    }
    *slash = L'\0';
    return path;
}

/* First line of the file, without its line ending; NULL if missing or empty. */
static wchar_t *first_line(const wchar_t *path)
{
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    LARGE_INTEGER size;
    char *bytes;
    DWORD got = 0;
    int length, wide;
    wchar_t *line;

    if (file == INVALID_HANDLE_VALUE)
        return NULL;
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > 0x100000) {
        CloseHandle(file);
        return NULL;
    }
    bytes = must(malloc((size_t)size.QuadPart));
    if (!ReadFile(file, bytes, (DWORD)size.QuadPart, &got, NULL))
        got = 0;
    CloseHandle(file);
    for (length = 0; (DWORD)length < got && bytes[length] != '\r' && bytes[length] != '\n'; length++)
        ;
    if (length == 0) {
        free(bytes);
        return NULL;
    }
    wide = MultiByteToWideChar(CP_UTF8, 0, bytes, length, NULL, 0);
    if (wide <= 0) {
        free(bytes);
        return NULL;
    }
    line = must(malloc(((size_t)wide + 1) * sizeof(wchar_t)));
    MultiByteToWideChar(CP_UTF8, 0, bytes, length, line, wide);
    line[wide] = L'\0';
    free(bytes);
    return line;
}

static void set_env(const wchar_t *name, const wchar_t *value)
{
    if (!SetEnvironmentVariableW(name, value)) {
        emit_error_code(L"cannot set an environment variable", GetLastError());
        ExitProcess(1);
    }
}

static void prepend_to_path(const wchar_t *folder)
{
    DWORD need = GetEnvironmentVariableW(L"PATH", NULL, 0);
    wchar_t *old = L"";
    wchar_t *value;

    if (need > 1) {
        old = must(malloc(need * sizeof(wchar_t)));
        GetEnvironmentVariableW(L"PATH", old, need);
        value = join(folder, L";", old);
        free(old);
    } else {
        value = join(folder, L"", L"");
    }
    set_env(L"PATH", value);
    free(value);
}

/* Python is killed when this process ends, including by a closed window. A
 * failure here is reported and the start goes on without the job. */
static HANDLE new_kill_on_close_job(void)
{
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
    HANDLE job = CreateJobObjectW(NULL, NULL);

    if (job == NULL) {
        emit_error_code(L"warning: no job object; Python will outlive a closed window", GetLastError());
        return NULL;
    }
    ZeroMemory(&limits, sizeof(limits));
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
        emit_error_code(L"warning: no job object; Python will outlive a closed window", GetLastError());
        CloseHandle(job);
        return NULL;
    }
    return job;
}

int wmain(void)
{
    wchar_t *dir = own_folder();
    wchar_t *python = join(dir, L"\\python\\python.exe", L"");
    wchar_t *engine_file = join(dir, L"\\lib\\engine\\ENGINE", L"");
    wchar_t *engine, *library, *engine_dir, *site, *command;
    const wchar_t *rest;
    DWORD attributes = GetFileAttributesW(python);
    STARTUPINFOW startup;
    PROCESS_INFORMATION child;
    HANDLE job;
    DWORD code;

    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY))
        return launcher_error(python, L" not found; the install is incomplete", L"");
    engine = first_line(engine_file);
    if (engine == NULL)
        return launcher_error(L"lib\\engine\\ENGINE is missing or empty", L"", L"");

    library = join(dir, L"\\lib\\engine\\", engine);
    engine_dir = join(dir, L"\\lib\\engine", L"");
    site = join(dir, L"\\lib\\site", L"");
    set_env(L"PYTHONNOUSERSITE", L"1");
    set_env(L"PYTHONPATH", site);
    set_env(L"TSB_TUI_TERM_LIBRARY", library);
    prepend_to_path(engine_dir);
    set_env(L"TSB_TUI_HOME", dir);

    rest = tsb_cmdline_rest(GetCommandLineW());
    command = join(L"\"", python, *rest ? L"\" -m tsb_tui " : L"\" -m tsb_tui");
    if (*rest) {
        wchar_t *with_rest = join(command, rest, L"");
        free(command);
        command = with_rest;
    }

    SetConsoleCtrlHandler(on_console_event, TRUE);
    job = new_kill_on_close_job();

    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    if (!CreateProcessW(python, command, NULL, NULL, TRUE, CREATE_SUSPENDED, NULL, NULL, &startup, &child)) {
        emit_error_code(L"cannot start Python", GetLastError());
        return 1;
    }
    if (job != NULL && !AssignProcessToJobObject(job, child.hProcess))
        emit_error_code(L"warning: Python is not in a job object; it will outlive a closed window", GetLastError());
    if (ResumeThread(child.hThread) == (DWORD)-1) {
        emit_error_code(L"cannot resume Python", GetLastError());
        TerminateProcess(child.hProcess, 1);
        return 1;
    }
    CloseHandle(child.hThread);
    WaitForSingleObject(child.hProcess, INFINITE);
    if (!GetExitCodeProcess(child.hProcess, &code)) {
        emit_error_code(L"cannot read Python's exit code", GetLastError());
        return 1;
    }
    return (int)code;
}
