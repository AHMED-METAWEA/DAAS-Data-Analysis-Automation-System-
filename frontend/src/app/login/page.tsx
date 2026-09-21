import { Login } from '@/components/sections/login'

export default function LoginPage() {
  const ssoProviders = {
    google: Boolean(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET),
    github: Boolean(process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET),
  }
  return <Login ssoProviders={ssoProviders} />
}
