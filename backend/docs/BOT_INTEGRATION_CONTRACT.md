# قرارداد یکپارچه‌سازی و ارتباط با بات‌های ناشر (Bot Integration Contract)

این سند مرجع رسمی و فنی برای هم‌تیمی‌هایی است که در حال پیاده‌سازی ربات‌های انتشار (تلگرام، یوتیوب، بله، روبیکا، اینستاگرام، توییتر و ...) در مخازن جداگانه هستند.

---

## ۱. معماری کلی سیستم (Decoupled Architecture)

```text
[Backend API] ──(زمانبندی / دیتابیس)──> [Scheduler]
                                              │
                                           (XADD)
                                              ▼
                               ┌─────────────────────────────┐
                               │  Redis Stream: publish_jobs │
                               └─────────────────────────────┘
                                              │
                               (XREADGROUP توسط بات‌های مستقل)
                                              ▼
                               ┌─────────────────────────────┐
                               │     Bot Runner / Worker     │
                               │   (پایتون، Go، Node.js و ...)│
                               └─────────────────────────────┘
                                              │
                                 انتشار در پلتفرم مقصد (API)
                                              │
                                              ▼
                             POST /internal/callback/publish-result
                             Authorization: Bearer <WORKER_TOKEN>
                                              │
                                              ▼
                             [Backend API: ثبت Idempotent وضعیت]
```

### مزایای این معماری:
1. **استقلال کامل زبان و مخزن (Language-Agnostic)**: ربات‌ها می‌توانند با Node.js، Go، Python، C# یا هر زبان دیگری توسعه داده شوند. نیازی به اجرای `arq` یا وارد کردن کدهای پایتونی این مخزن نیست.
2. **تضمین تحویل حداقل یک‌بار (At-least-once Delivery)**: پیام‌ها در صف Redis Stream باقی می‌مانند تا توسط بات با دستور `XACK` تایید شوند.
3. **عدم تکرار و امنیت (Idempotent & Secure)**: اندپوینت کال‌بک بک‌اند دارای جدول ثبت رویداد و بررسی `event_id` است. اگر بات به دلیل قطعی شبکه یک پیام را دوباره گزارش دهد، هیچ تکراری در دیتابیس رخ نمی‌دهد.

---

## ۲. دریافت تسک‌ها از صف ردیس (Redis Stream)

### اطلاعات اتصال:
- **نام استریم (Stream Key)**: `publish_jobs`
- **گروه مصرف‌کننده (Consumer Group)**: `bot_group` (یا نام دلخواه بات شما)

### گام اول: ساخت گروه مصرف‌کننده (فقط یک‌بار در شروع پروژه):
```bash
redis-cli XGROUP CREATE publish_jobs bot_group $ MKSTREAM
```

### گام دوم: خواندن تسک‌ها (Read Jobs):
```bash
redis-cli XREADGROUP GROUP bot_group worker_1 COUNT 5 BLOCK 2000 STREAMS publish_jobs >
```

### فیلدهای موجود در هر پیام استریم:
| فیلد | نوع داده | توضیحات |
| :--- | :--- | :--- |
| `job_id` | `string` (عددی) | شناسه منحصر‌به‌فرد جاب در سیستم اصلی (مثلاً `"125"`) |
| `platform` | `string` | کد پلتفرم هدف (`telegram`, `bale`, `rubika`, `youtube`, `instagram`) |
| `title` | `string` | عنوان یا موضوع پست (اختیاری) |
| `description` | `string` | متن یا کپشن کامل پست جهت انتشار |
| `credentials` | `string` | توکن ربات یا کلید احراز هویت دکریپت‌شده پلتفرم کاربر |
| `media_urls` | `JSON Array (string)` | آرایه‌ای از لینک‌های رسانه (عکس/ویدیو) جهت دانلود و انتشار |
| `extra_metadata`| `JSON Object (string)` | متادیتای اضافه (مانند تنظیمات کامنت، زمانبندی داخلی و ...) |
| `schema_version`| `string` | نسخه قرارداد (در حال حاضر `"1"`) |
| `dispatched_at` | `ISO 8601 String` | تاریخ و ساعت ارسال تسک به استریم |

### گام سوم: تایید پردازش تسک (Acknowledge):
پس از دریافت پیام، جهت حذف از لیست Pending:
```bash
redis-cli XACK publish_jobs bot_group <message_id>
```

---

## ۳. ارسال گزارش نتیجه به بک‌اند (Callback API)

پس از اینکه بات تلاش کرد پست را در پلتفرم مربوطه منتشر کند (چه با موفقیت و چه در صورت بروز خطا)، باید بلافاصله نتیجه را به اندپوینت کال‌بک بک‌اند گزارش دهد.

### مشخصات اندپوینت:
- **آدرس (URL)**:
  - `POST http://<BACKEND_HOST>:8000/internal/callback/publish-result`
  - یا: `POST http://<BACKEND_HOST>:8000/api/v1/internal/callback/publish-result`
- **هدر احراز هویت**:
  ```http
  Authorization: Bearer <WORKER_TOKEN>
  Content-Type: application/json
  ```
  *(مقدار `WORKER_TOKEN` در فایل `.env` سرور تعریف شده و به تیم ربات تحویل داده می‌شود)*

### اسکیمای بدنه درخواست (Request Body):
```json
{
  "event_id": "c7a84026-6f81-4b13-8d02-14a570bfa931",
  "job_id": 125,
  "status": "published",
  "platform_post_id": "msg_8849204",
  "error_message": null,
  "schema_version": "1"
}
```

#### فیلدها:
- `event_id` *(الزامی)*: یک شناسه یکتای UUIDv4 که توسط بات شما برای این تلاش ساخته می‌شود (جهت جلوگیری از اعمال مجدد در صورت Retry).
- `job_id` *(الزامی)*: همان `job_id` دریافتی از استریم.
- `status` *(الزامی)*: یکی از دو مقدار:
  - `"published"`: انتشار با موفقیت انجام شد.
  - `"failed"`: انتشار با خطا مواجه شد.
- `platform_post_id` *(اختیاری)*: شناسه پستی که پلتفرم مقصد برگردانده است (مثلاً شناسه پیام تلگرام یا ID ویدیوی یوتیوب).
- `error_message` *(در صورت خطا)*: متن خطا یا دلیلی که پلتفرم انتشار را رد کرد (حداکثر ۲۰۰۰ کاراکتر).

### نمونه پاسخ بک‌اند (Response):
#### وضعیت ۲۰۰ (پردازش موفق):
```json
{
  "status": "processed",
  "job_id": 125,
  "event_id": "c7a84026-6f81-4b13-8d02-14a570bfa931",
  "message": "Job status updated successfully."
}
```
#### وضعیت ۲۰۰ (درخواست تکراری / Idempotent):
```json
{
  "status": "duplicate_ignored",
  "job_id": 125,
  "event_id": "c7a84026-6f81-4b13-8d02-14a570bfa931",
  "message": "Event already processed."
}
```

---

## ۴. نمونه کدهای آماده برای تیم ربات‌ها

### الف) نمونه کد پایتون (Python Standalone Worker):
```python
import json
import uuid
import redis
import requests

REDIS_URL = "redis://127.0.0.1:6379/0"
BACKEND_URL = "http://127.0.0.1:8000/internal/callback/publish-result"
WORKER_TOKEN = "mezonflow-worker-secret-token-change-in-prod"

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# ۱. ساخت گروه (در صورت نیاز)
try:
    r.xgroup_create("publish_jobs", "telegram_bots", id="$", mkstream=True)
except redis.exceptions.ResponseError:
    pass  # قبلاً ساخته شده

print("🚀 Bot is listening for publish jobs...")

while True:
    # ۲. خواندن از استریم
    messages = r.xreadgroup("telegram_bots", "bot_worker_1", {"publish_jobs": ">"}, count=1, block=2000)
    if not messages:
        continue

    for stream_name, entries in messages:
        for message_id, data in entries:
            job_id = int(data["job_id"])
            platform = data["platform"]
            token = data["credentials"]
            text = data["description"]
            media = json.loads(data["media_urls"])

            print(f"📦 Processing job {job_id} for {platform}...")

            try:
                # ۳. انتشار واقعی در پلتفرم (مثلاً ارسال پیام تلگرام)
                # platform_post_id = send_telegram(token, text, media)
                platform_post_id = "tg_12345"
                status = "published"
                error = None
            except Exception as e:
                status = "failed"
                platform_post_id = None
                error = str(e)

            # ۴. ارسال کال‌بک به بک‌اند
            callback_payload = {
                "event_id": str(uuid.uuid4()),
                "job_id": job_id,
                "status": status,
                "platform_post_id": platform_post_id,
                "error_message": error,
                "schema_version": "1",
            }

            resp = requests.post(
                BACKEND_URL,
                headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
                json=callback_payload,
                timeout=10,
            )

            # ۵. تایید پیام در استریم
            if resp.status_code == 200:
                r.xack("publish_jobs", "telegram_bots", message_id)
                print(f"✅ Job {job_id} acknowledged.")
```

### ب) نمونه کد Node.js / TypeScript:
```typescript
import Redis from "ioredis";
import axios from "axios";
import { v4 as uuidv4 } from "uuid";

const redis = new Redis("redis://127.0.0.1:6379/0");
const BACKEND_URL = "http://127.0.0.1:8000/internal/callback/publish-result";
const WORKER_TOKEN = "mezonflow-worker-secret-token-change-in-prod";

async function runBot() {
  try {
    await redis.xgroup("CREATE", "publish_jobs", "node_bots", "$", "MKSTREAM");
  } catch (err) {}

  console.log("🚀 Node Bot listening on publish_jobs...");

  while (true) {
    const response = await redis.xreadgroup(
      "GROUP", "node_bots", "worker_node_1",
      "BLOCK", "2000", "COUNT", "1",
      "STREAMS", "publish_jobs", ">"
    );

    if (!response) continue;

    for (const [stream, messages] of response as any) {
      for (const [messageId, fields] of messages) {
        // تبدیل فیلدهای مسطح به شیء
        const data: Record<string, string> = {};
        for (let i = 0; i < fields.length; i += 2) {
          data[fields[i]] = fields[i + 1];
        }

        const jobId = parseInt(data.job_id);
        console.log(`Processing job ${jobId} for ${data.platform}`);

        let status = "published";
        let platformPostId: string | null = "post_999";
        let errorMessage: string | null = null;

        try {
          // انجام انتشار در API پلتفرم...
        } catch (err: any) {
          status = "failed";
          platformPostId = null;
          errorMessage = err.message;
        }

        // ارسال نتیجه به بک‌اند
        await axios.post(
          BACKEND_URL,
          {
            event_id: uuidv4(),
            job_id: jobId,
            status: status,
            platform_post_id: platformPostId,
            error_message: errorMessage,
            schema_version: "1"
          },
          {
            headers: { Authorization: `Bearer ${WORKER_TOKEN}` }
          }
        );

        await redis.xack("publish_jobs", "node_bots", messageId);
      }
    }
  }
}

runBot();
```
