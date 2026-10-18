#include "InterviewerSpeechDownload.h"

#if PLATFORM_WINDOWS
#include "Windows/WindowsHWrapper.h"
#include "Windows/AllowWindowsPlatformTypes.h"
THIRD_PARTY_INCLUDES_START
#include <winhttp.h>
THIRD_PARTY_INCLUDES_END
#include "Windows/HideWindowsPlatformTypes.h"
#endif

namespace UE::Interviewer
{
void FSpeechDownloadJob::Cancel()
{
    bCancelled.Store(true);
}

#if PLATFORM_WINDOWS
namespace
{
/** Synchronous WinHTTP handles are created, used and closed only on the downloader worker. */
struct FSpeechWinHttpHandle
{
    explicit FSpeechWinHttpHandle(HINTERNET InHandle) : Handle(InHandle) {}
    ~FSpeechWinHttpHandle() { if (Handle) WinHttpCloseHandle(Handle); }
    FSpeechWinHttpHandle(const FSpeechWinHttpHandle&) = delete;
    FSpeechWinHttpHandle& operator=(const FSpeechWinHttpHandle&) = delete;
    HINTERNET Handle;
};

}
#endif

FSpeechDownloadResult DownloadRemoteSpeech(const FSpeechDownloadAddress& Address, FSpeechDownloadJob& Job)
{
    FSpeechDownloadResult Result;
#if PLATFORM_WINDOWS
    if (!Address.bRemote || Job.IsCancelled()) return Result;
    const double Deadline = FPlatformTime::Seconds() + RemoteSpeechDownloadSeconds;
    FSpeechWinHttpHandle Session(WinHttpOpen(L"InterviewerSpeech/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0));
    constexpr int32 InitialStageMilliseconds = static_cast<int32>(RemoteSpeechDownloadSeconds * 1000) / 4;
    if (!Session.Handle || !WinHttpSetTimeouts(Session.Handle, InitialStageMilliseconds, InitialStageMilliseconds,
        InitialStageMilliseconds, InitialStageMilliseconds)) return Result;
    FSpeechWinHttpHandle Connection(WinHttpConnect(Session.Handle, *Address.Host, Address.Port, 0));
    if (!Connection.Handle || Job.IsCancelled()) return Result;
    FSpeechWinHttpHandle RequestScope(WinHttpOpenRequest(Connection.Handle, L"GET", *Address.RequestPath,
        nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE));
    const HINTERNET Request = RequestScope.Handle;
    if (!Request || Job.IsCancelled()) return Result;
    // IHttpRequest's Curl implementation follows redirects unconditionally. This separate native
    // transport rejects them before a second URL can be requested. TLS verification is never relaxed.
    DWORD Disabled = WINHTTP_DISABLE_REDIRECTS | WINHTTP_DISABLE_COOKIES | WINHTTP_DISABLE_AUTHENTICATION;
    if (!WinHttpSetOption(Request, WINHTTP_OPTION_DISABLE_FEATURE, &Disabled, sizeof(Disabled))) return Result;
    DWORD ConnectRetries = 1;
    if (!WinHttpSetOption(Request, WINHTTP_OPTION_CONNECT_RETRIES, &ConnectRetries, sizeof(ConnectRetries))) return Result;
    auto WithinDeadline = [&Job, Deadline, Request](bool bSending = false)
    {
        const double Remaining = Deadline - FPlatformTime::Seconds();
        if (Job.IsCancelled() || Remaining <= 0) return false;
        const int32 Milliseconds = FMath::Max(1, FMath::Min(15000, FMath::FloorToInt(Remaining * 1000)));
        // Send setup shares the remaining budget across resolve, connect, send and receive.
        // Response/read calls get only the remaining budget. Cancellation is observed between
        // native calls; every handle is released here after its current call returns.
        const int32 StageMilliseconds = bSending ? FMath::Max(1, Milliseconds / 4) : Milliseconds;
        DWORD ResponseMilliseconds = static_cast<DWORD>(StageMilliseconds);
        return !!WinHttpSetTimeouts(Request, StageMilliseconds, StageMilliseconds, StageMilliseconds, StageMilliseconds)
            && !!WinHttpSetOption(Request, WINHTTP_OPTION_RECEIVE_RESPONSE_TIMEOUT,
                &ResponseMilliseconds, sizeof(ResponseMilliseconds));
    };
    if (!WithinDeadline(true) || !WinHttpSendRequest(Request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
        WINHTTP_NO_REQUEST_DATA, 0, 0, 0) || !WithinDeadline() || !WinHttpReceiveResponse(Request, nullptr)) return Result;
    DWORD Status = 0;
    DWORD StatusSize = sizeof(Status);
    if (!WinHttpQueryHeaders(Request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
        WINHTTP_HEADER_NAME_BY_INDEX, &Status, &StatusSize, WINHTTP_NO_HEADER_INDEX) || Status != 200) return Result;
    TCHAR ContentType[128] = {};
    DWORD ContentTypeSize = sizeof(ContentType);
    if (!WinHttpQueryHeaders(Request, WINHTTP_QUERY_CONTENT_TYPE, WINHTTP_HEADER_NAME_BY_INDEX,
        ContentType, &ContentTypeSize, WINHTTP_NO_HEADER_INDEX)
        || !FString(ContentType).Equals(TEXT("audio/wav"), ESearchCase::IgnoreCase)) return Result;
    constexpr int32 ChunkBytes = 16384;
    uint8 Chunk[ChunkBytes];
    while (WithinDeadline())
    {
        DWORD Available = 0;
        if (!WinHttpQueryDataAvailable(Request, &Available) || !WithinDeadline()) return Result;
        if (Available == 0)
        {
            if (!Job.IsCancelled() && FPlatformTime::Seconds() <= Deadline
                && IsSpeechWaveResponse(Status, ContentType, Result.Wave.Num())) Result.bSucceeded = true;
            return Result;
        }
        // Read only bytes already buffered, so a slow peer cannot keep one read filling 16 KiB
        // while repeatedly resetting the receive timeout without another deadline check.
        const DWORD ToRead = FMath::Min(Available, static_cast<DWORD>(ChunkBytes));
        DWORD Received = 0;
        if (!WinHttpReadData(Request, Chunk, ToRead, &Received) || Received == 0) return Result;
        if (Received > static_cast<DWORD>(MaxSpeechWaveBytes - Result.Wave.Num())) return Result;
        Result.Wave.Append(Chunk, Received);
    }
#endif
    return Result;
}
}
