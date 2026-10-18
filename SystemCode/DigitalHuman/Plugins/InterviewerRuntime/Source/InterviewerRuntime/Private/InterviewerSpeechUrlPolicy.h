#pragma once

#include "CoreMinimal.h"

namespace UE::Interviewer
{
constexpr int32 MaxSpeechTokenCharacters = 255;
constexpr int32 MaxSpeechWaveBytes = 5760044;
constexpr double RemoteSpeechDownloadSeconds = 15.0;

/** Parsed only after exact URL admission; RequestPath may contain a token and must never be logged. */
struct FSpeechDownloadAddress
{
    bool bRemote = false;
    FString Host;
    uint16 Port = 443;
    FString RequestPath;
};

/** Deliberately accept only ASCII URL-safe base64 characters, not encoded separators or Unicode. */
inline bool IsSpeechBase64Character(TCHAR Character)
{
    return (Character >= TEXT('a') && Character <= TEXT('z'))
        || (Character >= TEXT('A') && Character <= TEXT('Z'))
        || (Character >= TEXT('0') && Character <= TEXT('9'))
        || Character == TEXT('-') || Character == TEXT('_');
}

/** Validate the bounded payload:base62_timestamp:signature shape; the server verifies the signature. */
inline bool IsBoundedSpeechToken(const FString& EncodedToken)
{
    if (EncodedToken.Len() > MaxSpeechTokenCharacters + 4) return false;
    FString Token = EncodedToken;
    if (Token.Contains(TEXT("%")))
    {
        // Django urlencode escapes ':' as %3A. Decode that separator only, exactly once;
        // never accept encoded path/query delimiters or mixed raw/encoded separators.
        if (Token.Contains(TEXT(":"))) return false;
        Token = Token.Replace(TEXT("%3A"), TEXT(":"), ESearchCase::CaseSensitive);
    }
    if (Token.IsEmpty() || Token.Len() > MaxSpeechTokenCharacters) return false;
    int32 Segment = 0;
    int32 SegmentLength = 0;
    for (TCHAR Character : Token)
    {
        if (Character == TEXT(':'))
        {
            if (SegmentLength == 0 || Segment >= 2) return false;
            ++Segment;
            SegmentLength = 0;
        }
        else
        {
            if (!IsSpeechBase64Character(Character)
                || (Segment == 1 && (Character == TEXT('-') || Character == TEXT('_')))) return false;
            ++SegmentLength;
        }
    }
    return Segment == 2 && SegmentLength == 43;
}

/** Parse an operator-supplied HTTPS origin only; no credentials, path, wildcard or IPv6 shorthand. */
inline bool ParseSpeechOrigin(const FString& Origin, FString& Host, uint16& Port)
{
    Host.Empty();
    Port = 443;
    if (!Origin.StartsWith(TEXT("https://"), ESearchCase::CaseSensitive) || Origin.Len() > 255) return false;
    FString Authority = Origin.Mid(8);
    FString PortText;
    int32 Colon = INDEX_NONE;
    if (Authority.FindChar(TEXT(':'), Colon))
    {
        PortText = Authority.Mid(Colon + 1);
        Authority = Authority.Left(Colon);
        if (PortText.IsEmpty() || PortText.Len() > 5) return false;
        uint32 Number = 0;
        for (TCHAR Character : PortText)
        {
            if (Character < TEXT('0') || Character > TEXT('9')) return false;
            Number = Number * 10 + Character - TEXT('0');
        }
        if (Number == 0 || Number > 65535 || FString::FromInt(Number) != PortText) return false;
        Port = static_cast<uint16>(Number);
    }
    if (Authority.IsEmpty() || Authority.Len() > 253) return false;
    int32 LabelLength = 0;
    TCHAR Previous = 0;
    for (TCHAR Character : Authority)
    {
        if (Character == TEXT('.'))
        {
            if (LabelLength == 0 || Previous == TEXT('-')) return false;
            LabelLength = 0;
        }
        else
        {
            if (!IsSpeechBase64Character(Character) || Character == TEXT('_')
                || (LabelLength == 0 && Character == TEXT('-')) || ++LabelLength > 63) return false;
        }
        Previous = Character;
    }
    if (LabelLength == 0 || Previous == TEXT('-')) return false;
    Host = Authority;
    return true;
}

/** Admit one exact UUID path: legacy local URLs have no query; trusted HTTPS needs exactly one token. */
inline bool ValidateSpeechDownloadUrl(const FString& Url, const FString& UtteranceId,
    const FString& TrustedOrigin, FSpeechDownloadAddress& Address)
{
    Address = FSpeechDownloadAddress();
    FGuid Capability;
    if (UtteranceId.Len() != 36 || !FGuid::ParseExact(UtteranceId, EGuidFormats::DigitsWithHyphens, Capability)) return false;
    const FString Path = TEXT("/api/speech/audio/") + UtteranceId + TEXT("/");
    if (Url == TEXT("http://127.0.0.1:8765") + Path || Url == TEXT("http://localhost:8765") + Path)
    {
        Address.RequestPath = Path;
        return true;
    }
    FString Host;
    uint16 Port = 443;
    if (!ParseSpeechOrigin(TrustedOrigin, Host, Port)) return false;
    const FString Prefix = TrustedOrigin + Path + TEXT("?token=");
    if (!Url.StartsWith(Prefix, ESearchCase::CaseSensitive) || !IsBoundedSpeechToken(Url.Mid(Prefix.Len()))) return false;
    Address.bRemote = true;
    Address.Host = MoveTemp(Host);
    Address.Port = Port;
    Address.RequestPath = Url.Mid(TrustedOrigin.Len());
    return true;
}

/** HTTP metadata is checked before accepting bytes; DecodeWave still validates actual PCM content. */
inline bool IsSpeechWaveResponse(int32 Status, const FString& ContentType, int32 ByteCount)
{
    return Status == 200 && ContentType.Equals(TEXT("audio/wav"), ESearchCase::IgnoreCase)
        && ByteCount >= 44 && ByteCount <= MaxSpeechWaveBytes;
}
}
