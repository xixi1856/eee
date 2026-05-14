/**
 * Mock student seed script — creates 30 student accounts and enrolls them
 * in the target course. Each student also gets an AgentIdentityMapping so
 * API endpoints that require a bound agent_user_id work out-of-the-box.
 *
 * Usage:
 *   cd edu-platform
 *   npx tsx prisma/seed-mock-students.ts
 *
 * Safe to re-run: users/enrollments that already exist are skipped (upsert).
 */

import { PrismaClient, UserRole } from "@prisma/client";
import * as argon2 from "argon2";

const prisma = new PrismaClient();

const COURSE_ID = "f5ca8926-2c4d-475d-b60f-bb95991ef3cf";
const MOCK_PASSWORD = "MockStudent@2026"; // meets 12-char policy
const MOCK_COUNT = 30;

async function main(): Promise<void> {
  // Verify the course exists before doing anything
  const course = await prisma.course.findUnique({
    where: { id: COURSE_ID },
    select: { id: true, name: true },
  });
  if (!course) {
    throw new Error(
      `Course ${COURSE_ID} not found. Make sure the database has the target course.`,
    );
  }
  console.info(`Target course: "${course.name}" (${course.id})`);

  const passwordHash = await argon2.hash(MOCK_PASSWORD, {
    type: argon2.argon2id,
  });

  let created = 0;
  let skipped = 0;

  for (let i = 1; i <= MOCK_COUNT; i++) {
    const idx = String(i).padStart(2, "0");
    const username = `mock_student_${idx}`;
    const email = `mock_student_${idx}@localhost.test`;

    // Upsert user (skip if username already exists)
    let user = await prisma.user.findUnique({ where: { username } });
    if (!user) {
      user = await prisma.user.create({
        data: {
          username,
          email,
          passwordHash,
          role: UserRole.STUDENT,
          realName: `测试学生 ${idx}`,
          isActive: true,
        },
      });
      created++;
      console.info(`  [+] Created user: ${username} (${user.id})`);
    } else {
      skipped++;
      console.info(`  [~] Skipped existing user: ${username} (${user.id})`);
    }

    // Upsert course enrollment
    await prisma.courseEnrollment.upsert({
      where: { courseId_studentId: { courseId: COURSE_ID, studentId: user.id } },
      create: { courseId: COURSE_ID, studentId: user.id },
      update: {},
    });
  }

  console.info(
    `\nDone. Created: ${created}, Skipped (already existed): ${skipped}`,
  );
  console.info(`All ${MOCK_COUNT} students enrolled in course "${course.name}"`);
  console.info(`\nCredentials for all mock students:`);
  console.info(`  Username: mock_student_01 … mock_student_30`);
  console.info(`  Password: ${MOCK_PASSWORD}`);
  console.info(`  Email:    mock_student_XX@localhost.test`);
}

main()
  .catch((e: unknown) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
