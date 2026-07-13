import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();

function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8');
}

function expectContains(file, needle, reason) {
  const body = read(file);
  if (!body.includes(needle)) {
    throw new Error(`${file} missing ${needle}: ${reason}`);
  }
}

function expectRegex(file, regex, reason) {
  const body = read(file);
  if (!regex.test(body)) {
    throw new Error(`${file} missing ${regex}: ${reason}`);
  }
}

expectContains('src/views/CourseManagement.vue', 'courseKeyword', 'course-management needs an inline keyword filter');
expectContains('src/views/CourseManagement.vue', 'qualityRows', 'course-management needs quality preview metrics');
expectContains('src/views/CourseManagement.vue', 'openInsightsForCourse', 'row actions must jump to course quality analysis');
expectContains('src/views/CourseManagement.vue', 'openLearnerFilter', 'course-management must expose child/personality filtering entry');
expectContains('src/views/CourseManagement.vue', 'course.qualityPreview', 'quality dashboard copy must be localized');
expectContains('src/views/CourseManagement.vue', '@media (max-width: 720px)', 'course-management must adapt for mobile');

expectContains('src/views/CourseInsights.vue', 'this.$route.query.keyword', 'insights should hydrate keyword from route links');
expectContains('src/views/CourseInsights.vue', 'this.$route.query.courseId', 'insights should hydrate courseId from route links');

expectContains('src/views/LessonMonitoring.vue', 'filters.keyword', 'lesson monitoring needs keyword filtering');
expectContains('src/views/LessonMonitoring.vue', 'this.$route.query.keyword', 'lesson monitoring deep links should hydrate keyword filtering');
expectContains('src/apis/module/monitoring.js', 'keyword=', 'monitoring API must send keyword to backend');

expectContains('src/views/DeviceManagement.vue', 'device.childProfile', 'device admin must show the child profile column');
expectContains('src/views/DeviceManagement.vue', 'childName: device.childName', 'device admin must map childName from ESP server API');
expectContains('src/views/DeviceManagement.vue', 'childAge: device.childAge', 'device admin must map childAge from ESP server API');
expectContains('src/views/DeviceManagement.vue', 'updateChildProfile(row)', 'device admin must allow direct child profile edits');
expectRegex(
  'src/views/DeviceManagement.vue',
  /\{\s*alias:\s*child\.childName,\s*childName:\s*child\.childName,\s*childAge:\s*child\.age,\s*childInterests,\s*learningStyle:\s*personality\.learningStyle\s*\|\|\s*'',\s*vocabularyLevel:\s*personality\.vocabularyLevel\s*\|\|\s*'',\s*parentCareer:\s*personality\.parentCareer\s*\|\|\s*'',\s*\}/m,
  'use-child action must persist alias + full child profile/personality together',
);
expectContains('src/i18n/vi.js', "'device.childProfile'", 'Vietnamese device admin copy must include child profile');
expectContains('src/i18n/en.js', "'device.childProfile'", 'English device admin copy must include child profile');

expectContains('src/i18n/vi.js', "'course.openLearners'", 'Vietnamese course-management copy must include learner filter CTA');
expectContains('src/i18n/en.js', "'course.openLearners'", 'English course-management copy must include learner filter CTA');
expectRegex('src/views/CourseManagement.vue', /grid-template-columns:\s*repeat\(auto-fit,/m, 'stat cards should use responsive grid tracks');

console.log('course admin UI contract OK');
