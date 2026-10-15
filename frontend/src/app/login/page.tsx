import { Suspense } from 'react'
import { SignIn } from './SignIn'

export default function LoginPage() {
  return (
    <Suspense>
      <SignIn />
    </Suspense>
  )
}
