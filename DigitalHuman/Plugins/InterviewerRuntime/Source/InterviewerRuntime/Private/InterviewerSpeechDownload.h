#pragma once

#include "CoreMinimal.h"
#include "Templates/Atomic.h"
#include "InterviewerSpeechUrlPolicy.h"

namespace UE::Interviewer
{
/** Fence remote results immediately; all native handles remain owned by the downloader worker. */
class FSpeechDownloadJob
{
public:
    void Cancel();
    bool IsCancelled() const { return bCancelled.Load(); }

private:
    TAtomic<bool> bCancelled{false};
};

/** Return only bounded WAV bytes and a success flag; neither URLs nor native error details escape. */
struct FSpeechDownloadResult
{
    bool bSucceeded = false;
    TArray<uint8> Wave;
};

/** Run on a worker, with strict TLS and disabled redirects, cookies and automatic authentication. */
FSpeechDownloadResult DownloadRemoteSpeech(const FSpeechDownloadAddress& Address, FSpeechDownloadJob& Job);
}
