-- Migration 190: the first «رسائل ريحان» message back in the owner's own layout.
--
-- 189 seeded it reshaped (bullets, bold, merged lines). Owner 2026-10-26: "use the
-- same msg, do not change the format, u can edit the words only". So this is the
-- owner's text line for line; the only edits are spelling (hamza, «إلى», Arabic
-- commas) and the numbers the draft left blank. Single newlines are intentional —
-- both clients render them as line breaks (see contracts/announcements.ts).

UPDATE public.announcements
   SET body_md =
       E'طورنا ريحان من ناحية زمن الاستجابة والجودة\n\n'
       'استعملنا نماذج أحسن وأسرع لخدمتكم مع تحسن معتبر بجودة الإجابات، التحسن طال جميع الوكلاء الأساسيين: الباحث والكاتب\n\n'
       'مثال: الباحث المعمق كان يخلص في 3-5 دقائق\n'
       'صار يخلص في 1 ونص إلى 3 ونص دقيقة\n\n'
       'كان يستهلك 3-7 نقاط\n'
       'صار يستهلك من 5-10 نقاط\n\n'
       'وعلى أثر هذا التغيير زاد استخدام البيانات، فقررنا نرفع جميع حدود الاستخدام على جميع الاشتراكات المدفوعة\n\n'
       'الآن الباقة الأساسية ارتفعت من 10 و 50 إلى 15 نقطة للجلسة و 75 أسبوعياً\n'
       'والاحترافية من 15 و 75 إلى 20 نقطة للجلسة و 100 أسبوعياً\n'
       'والقصوى من 75 و 375 إلى 100 نقطة للجلسة و 500 أسبوعياً'
 WHERE title = 'طوّرنا ريحان: أسرع وأدق';
