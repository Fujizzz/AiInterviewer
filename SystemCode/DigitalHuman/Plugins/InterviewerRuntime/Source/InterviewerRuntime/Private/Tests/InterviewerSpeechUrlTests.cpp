#include "Misc/AutomationTest.h"

#include "InterviewerSpeechDownload.h"
#include "InterviewerSpeechUrlPolicy.h"

#if WITH_DEV_AUTOMATION_TESTS

using namespace UE::Interviewer;
namespace
{
constexpr EAutomationTestFlags SpeechUrlFlags = EAutomationTestFlags::EditorContext
    | EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter;
const FString SpeechId(TEXT("aeb59ac0-95b5-48a8-bc57-351cf16a37a6"));
const FString OtherSpeechId(TEXT("649b87cd-8b75-4d28-949e-9035cd456c22"));
const FString TrustedSpeechOrigin(TEXT("https://47.239.50.129"));

/** Synthetic signing-shaped data only; no production token or secret is used. */
FString SpeechToken() { return TEXT("cGF5bG9hZA:1vAb9:") + FString::ChrN(43, TEXT('s')); }
FString SpeechPath(const FString& Id = SpeechId) { return TEXT("/api/speech/audio/") + Id + TEXT("/"); }
FString RemoteSpeechUrl() { return TrustedSpeechOrigin + SpeechPath() + TEXT("?token=") + SpeechToken(); }
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechUrlLocal,
    "Interviewer.SpeechUrl.Local", SpeechUrlFlags);

bool FInterviewerSpeechUrlLocal::RunTest(const FString& Parameters)
{
    FSpeechDownloadAddress Address;
    for (const FString& Origin : {FString(TEXT("http://127.0.0.1:8765")), FString(TEXT("http://localhost:8765"))})
    {
        TestTrue(TEXT("Exact legacy URL remains enabled without remote configuration"),
            ValidateSpeechDownloadUrl(Origin + SpeechPath(), SpeechId, TEXT(""), Address));
        TestFalse(TEXT("Local admission preserves the legacy HTTP transport"), Address.bRemote);
        TestEqual(TEXT("Local URL uses the exact matching UUID path"), Address.RequestPath, SpeechPath());
        for (const FString& Suffix : {FString(TEXT("?token=")) + SpeechToken(), FString(TEXT("?x=1")),
            FString(TEXT("#fragment")), FString(TEXT("#")), FString(TEXT("../")), FString(TEXT("/"))})
            TestFalse(TEXT("Local URL rejects query, fragment and alternate path forms"),
                ValidateSpeechDownloadUrl(Origin + SpeechPath() + Suffix, SpeechId, TEXT(""), Address));
    }
    for (const FString& Origin : {FString(TEXT("http://127.0.0.1:8766")), FString(TEXT("http://user@localhost:8765")),
        FString(TEXT("http://localhost:8765.evil.example")), FString(TEXT("http://127.1:8765")),
        FString(TEXT("https://localhost:8765")), FString(TEXT("http://[::1]:8765"))})
        TestFalse(TEXT("Legacy admission does not widen hosts, ports or schemes"),
            ValidateSpeechDownloadUrl(Origin + SpeechPath(), SpeechId, TEXT(""), Address));
    TestFalse(TEXT("A second UUID cannot borrow the first utterance"),
        ValidateSpeechDownloadUrl(TEXT("http://127.0.0.1:8765") + SpeechPath(OtherSpeechId), SpeechId, TEXT(""), Address));
    TestFalse(TEXT("Malformed utterance IDs fail before an HTTP request"),
        ValidateSpeechDownloadUrl(TEXT("http://127.0.0.1:8765/api/speech/audio/not-a-guid/"), TEXT("not-a-guid"), TEXT(""), Address));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechUrlRemote,
    "Interviewer.SpeechUrl.Remote", SpeechUrlFlags);

bool FInterviewerSpeechUrlRemote::RunTest(const FString& Parameters)
{
    FSpeechDownloadAddress Address;
    TestFalse(TEXT("HTTPS is not automatically trusted without operator opt-in"),
        ValidateSpeechDownloadUrl(RemoteSpeechUrl(), SpeechId, TEXT(""), Address));
    TestTrue(TEXT("Explicit trusted origin admits one exact signed audio path"),
        ValidateSpeechDownloadUrl(RemoteSpeechUrl(), SpeechId, TrustedSpeechOrigin, Address));
    TestTrue(TEXT("Trusted HTTPS uses the native no-redirect downloader"), Address.bRemote);
    TestEqual(TEXT("Only the configured host reaches WinHTTP"), Address.Host, FString(TEXT("47.239.50.129")));
    TestEqual(TEXT("Implicit HTTPS uses port 443"), Address.Port, static_cast<uint16>(443));
    TestEqual(TEXT("The admitted query is retained for server-side verification"),
        Address.RequestPath, SpeechPath() + TEXT("?token=") + SpeechToken());
    const FString EncodedToken = SpeechToken().Replace(TEXT(":"), TEXT("%3A"));
    TestTrue(TEXT("Canonical Django urlencode separators are admitted without general decoding"),
        ValidateSpeechDownloadUrl(TrustedSpeechOrigin + SpeechPath() + TEXT("?token=") + EncodedToken,
            SpeechId, TrustedSpeechOrigin, Address));
    TestEqual(TEXT("Wire encoding reaches the backend unchanged"),
        Address.RequestPath, SpeechPath() + TEXT("?token=") + EncodedToken);
    const FString PortOrigin = TrustedSpeechOrigin + TEXT(":8443");
    TestTrue(TEXT("A non-default port is permitted only when explicitly configured"),
        ValidateSpeechDownloadUrl(PortOrigin + SpeechPath() + TEXT("?token=") + SpeechToken(), SpeechId, PortOrigin, Address));
    TestEqual(TEXT("Configured port is preserved"), Address.Port, static_cast<uint16>(8443));
    TestFalse(TEXT("Explicit default-port syntax does not bypass exact origin matching"),
        ValidateSpeechDownloadUrl(TrustedSpeechOrigin + TEXT(":443") + SpeechPath() + TEXT("?token=") + SpeechToken(),
            SpeechId, TrustedSpeechOrigin, Address));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechUrlBoundaries,
    "Interviewer.SpeechUrl.Boundaries", SpeechUrlFlags);

bool FInterviewerSpeechUrlBoundaries::RunTest(const FString& Parameters)
{
    FSpeechDownloadAddress Address;
    for (const FString& Origin : {FString(TEXT("https://other.example")), FString(TEXT("http://47.239.50.129")),
        FString(TEXT("https://47.239.50.129:8443")), FString(TEXT("https://user:password@47.239.50.129")),
        FString(TEXT("https://47.239.50.129.evil.example")), FString(TEXT("https://47.239.50.129@evil.example"))})
        TestFalse(TEXT("Remote URL cannot change origin, transport, port or credentials"),
            ValidateSpeechDownloadUrl(Origin + SpeechPath() + TEXT("?token=") + SpeechToken(), SpeechId, TrustedSpeechOrigin, Address));
    for (const FString& Path : {SpeechPath(OtherSpeechId), FString(TEXT("/api/speech/audio/../")) + SpeechId + TEXT("/"),
        FString(TEXT("/api/speech/audio/%2e%2e/")) + SpeechId + TEXT("/"),
        SpeechPath() + TEXT("extra/"), SpeechPath().LeftChop(1), FString(TEXT("//api/speech/audio/")) + SpeechId + TEXT("/")})
        TestFalse(TEXT("Remote URL rejects mismatched UUID and alternate path forms"),
            ValidateSpeechDownloadUrl(TrustedSpeechOrigin + Path + TEXT("?token=") + SpeechToken(), SpeechId, TrustedSpeechOrigin, Address));
    for (const FString& Query : {FString(TEXT("")), FString(TEXT("?token=")), FString(TEXT("?other=")) + SpeechToken(),
        FString(TEXT("?token=")) + SpeechToken() + TEXT("&token=") + SpeechToken(),
        FString(TEXT("?token=")) + SpeechToken() + TEXT("&x=1"),
        FString(TEXT("?token=")) + SpeechToken() + TEXT("#"),
        FString(TEXT("?token=")) + SpeechToken() + TEXT("#fragment"),
        FString(TEXT("?token=")) + SpeechToken() + TEXT("%26x%3D1")})
        TestFalse(TEXT("Exactly one bounded token query is required, with no fragment or extra fields"),
            ValidateSpeechDownloadUrl(TrustedSpeechOrigin + SpeechPath() + Query, SpeechId, TrustedSpeechOrigin, Address));
    for (const FString& Origin : {FString(TEXT("https://47.239.50.129/")), FString(TEXT("https://47.239.50.129/path")),
        FString(TEXT("https://user@47.239.50.129")), FString(TEXT("https://*.example")),
        FString(TEXT("https://47.239.50.129?x=1")), FString(TEXT("https://47.239.50.129#")),
        FString(TEXT("https://example..com")), FString(TEXT("https://-host.example")),
        FString(TEXT("https://47.239.50.129:0")), FString(TEXT("https://47.239.50.129:65536")),
        FString(TEXT("https://47.239.50.129:0443")), FString(TEXT("https://47.239.50.129\n"))})
        TestFalse(TEXT("Invalid operator origins do not enable arbitrary public downloads"),
            ValidateSpeechDownloadUrl(RemoteSpeechUrl(), SpeechId, Origin, Address));
    for (const FString& Token : {FString(TEXT("")), FString(TEXT("payload:timestamp:short")),
        FString(TEXT("payload::")) + FString::ChrN(43, TEXT('s')),
        FString(TEXT("payload:1vAb9:")) + FString::ChrN(44, TEXT('s')),
        FString(TEXT("payload%253A1vAb9%253A")) + FString::ChrN(43, TEXT('s')),
        FString(TEXT("payload%3a1vAb9%3a")) + FString::ChrN(43, TEXT('s')),
        FString(TEXT("payload%3A1vAb9:")) + FString::ChrN(43, TEXT('s')),
        FString(TEXT("payload:1vAb9:")) + FString::ChrN(42, TEXT('s')) + TEXT("+"),
        FString(TEXT("payload:1vAb9:")) + FString::ChrN(42, TEXT('s')) + TEXT("="),
        FString(TEXT("payload:1vAb9:")) + FString::ChrN(42, TEXT('s')) + TEXT("\n")})
        TestFalse(TEXT("Malformed tokens cannot introduce escaping or alternative signing shapes"), IsBoundedSpeechToken(Token));
    const FString Bounded = FString::ChrN(205, TEXT('p')) + TEXT(":1vAb9:") + FString::ChrN(43, TEXT('s'));
    TestEqual(TEXT("Boundary fixture has the maximum decoded token length"), Bounded.Len(), MaxSpeechTokenCharacters);
    TestTrue(TEXT("Maximum bounded token is accepted"), IsBoundedSpeechToken(Bounded));
    TestTrue(TEXT("Maximum token also admits canonical encoded colons"), IsBoundedSpeechToken(Bounded.Replace(TEXT(":"), TEXT("%3A"))));
    TestFalse(TEXT("An overlength token is rejected before transport"), IsBoundedSpeechToken(TEXT("p") + Bounded));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FInterviewerSpeechDownloadBounds,
    "Interviewer.SpeechUrl.DownloadBounds", SpeechUrlFlags);

bool FInterviewerSpeechDownloadBounds::RunTest(const FString& Parameters)
{
    TestTrue(TEXT("Valid WAV metadata is accepted for content validation"), IsSpeechWaveResponse(200, TEXT("audio/wav"), 44));
    TestTrue(TEXT("WAV content type is case insensitive"), IsSpeechWaveResponse(200, TEXT("Audio/Wav"), MaxSpeechWaveBytes));
    TestFalse(TEXT("Oversize WAV is never handed to the solver"), IsSpeechWaveResponse(200, TEXT("audio/wav"), MaxSpeechWaveBytes + 1));
    TestFalse(TEXT("Truncated WAV is rejected"), IsSpeechWaveResponse(200, TEXT("audio/wav"), 43));
    TestFalse(TEXT("Login HTML cannot masquerade as audio"), IsSpeechWaveResponse(200, TEXT("text/html"), 1000));
    TestFalse(TEXT("HTTP redirects are not accepted as audio"), IsSpeechWaveResponse(302, TEXT("audio/wav"), 1000));
    TestFalse(TEXT("Authentication failures cannot reach the solver"), IsSpeechWaveResponse(401, TEXT("audio/wav"), 1000));
    FSpeechDownloadJob Job;
    TestFalse(TEXT("A fresh download is not cancelled"), Job.IsCancelled());
    Job.Cancel();
    Job.Cancel();
    TestTrue(TEXT("Cancellation is idempotent and permanently fences the request"), Job.IsCancelled());
    return true;
}

#endif
