import Link from 'next/link'
import Image from "next/image";

const NavTitle = () => {
    return (
      <div className="flex items-center">
        <Link href="/" tabIndex={0} aria-label="Go to homepage" >
          <div
            className="flex items-center cursor-pointer"
            tabIndex={-1}
            aria-hidden="true"
          >
            <Image
              src="/logo_white.png"
              width={64}
              height={64}
              alt="交易辅助系统 Logo"
            />
            <span className="ml-2 text-lg font-medium hidden sm:inline-block">
              交易辅助系统
            </span>
          </div>
        </Link>
      </div>
    );
}

export default NavTitle;