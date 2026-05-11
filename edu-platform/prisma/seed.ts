import { PrismaClient, UserRole } from "@prisma/client";
import * as argon2 from "argon2";

const prisma = new PrismaClient();

/**
 * Bootstrap admin user (server-side only). Password from env — never log it.
 * Run: `npm run db:seed`
 */
async function main(): Promise<void> {
  const username = process.env.SEED_ADMIN_USERNAME ?? "admin";
  const password = process.env.SEED_ADMIN_PASSWORD;
  const email = process.env.SEED_ADMIN_EMAIL ?? "admin@localhost";

  if (!password || password.length < 12) {
    throw new Error(
      "SEED_ADMIN_PASSWORD is required and must be at least 12 characters",
    );
  }

  const existing = await prisma.user.findUnique({ where: { username } });
  if (existing) {
    console.info("Seed skipped: admin username already exists");
    return;
  }

  const passwordHash = await argon2.hash(password, {
    type: argon2.argon2id,
  });

  await prisma.user.create({
    data: {
      username,
      email,
      passwordHash,
      role: UserRole.ADMIN,
      realName: "System Admin",
      isActive: true,
    },
  });

  console.info("Seed completed: admin user created");
}

main()
  .catch((e: unknown) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
